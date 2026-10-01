import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Sequence

from mini_pi.verification import sanitized_environment


MAX_COMMAND_OUTPUT = 20_000


class CommandDecision(str, Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


ALLOWED_COMMAND_PREFIXES = (
    ("python", "-m", "unittest"),
    ("python", "-m", "pytest"),
    ("python", "-m", "compileall"),
    ("python", "-m", "ruff"),
    ("python", "-m", "mypy"),
    ("git", "diff"),
    ("git", "status"),
    ("git", "log"),
    ("git", "show"),
)

CONFIRM_COMMAND_PREFIXES = (
    ("python", "-m", "pip", "install"),
    ("pip", "install"),
    ("pip3", "install"),
    ("git", "fetch"),
    ("git", "pull"),
    ("curl",),
    ("wget",),
)

DENIED_EXECUTABLES = {
    "bash",
    "chmod",
    "chown",
    "dd",
    "fish",
    "kill",
    "killall",
    "mv",
    "osascript",
    "powershell",
    "pwsh",
    "rm",
    "sh",
    "sudo",
    "zsh",
}

DENIED_GIT_SUBCOMMANDS = {
    "checkout",
    "clean",
    "commit",
    "push",
    "reset",
    "restore",
}


class CommandPolicyError(ValueError):
    """命令参数不合法或被安全策略拒绝。"""


@dataclass(frozen=True, slots=True)
class CommandAssessment:
    decision: CommandDecision
    command: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class CommandResult:
    command: tuple[str, ...]
    exit_code: int
    timed_out: bool
    elapsed_seconds: float
    output: str
    decision: CommandDecision


def _has_prefix(command: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    return len(command) >= len(prefix) and command[: len(prefix)] == prefix


class CommandPolicy:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _canonical(self, command: Sequence[str]) -> tuple[str, ...]:
        if isinstance(command, (str, bytes)) or not isinstance(command, Sequence):
            raise CommandPolicyError("command 必须是字符串数组")
        if not command:
            raise CommandPolicyError("command 不能为空")
        if not all(isinstance(item, str) and item for item in command):
            raise CommandPolicyError("command 中每一项都必须是非空字符串")

        executable = Path(command[0]).name
        python_names = {"python", "python3", Path(sys.executable).name}

        if executable in python_names:
            executable = "python"
        elif executable == "pytest":
            return ("python", "-m", "pytest", *command[1:])

        return (executable, *command[1:])

    def _paths_are_safe(self, command: tuple[str, ...]) -> bool:
        for argument in command[1:]:
            candidate = (
                argument.split("=", 1)[1]
                if argument.startswith("--") and "=" in argument
                else argument
            )

            if candidate.startswith(("http://", "https://")):
                continue

            supplied = Path(candidate)
            if not supplied.is_absolute() and ".." not in supplied.parts:
                continue

            try:
                path = (
                    supplied.resolve()
                    if supplied.is_absolute()
                    else (self.root / supplied).resolve()
                )
                path.relative_to(self.root)
            except ValueError:
                return False
        return True

    def assess(self, command: Sequence[str]) -> CommandAssessment:
        canonical = self._canonical(command)
        executable = canonical[0]

        if executable in DENIED_EXECUTABLES:
            return CommandAssessment(
                CommandDecision.DENY,
                canonical,
                f"危险命令被拒绝：{executable}",
            )

        if executable == "git" and len(canonical) > 1:
            if canonical[1] in DENIED_GIT_SUBCOMMANDS:
                return CommandAssessment(
                    CommandDecision.DENY,
                    canonical,
                    f"Git 写操作被拒绝：{canonical[1]}",
                )

        if not self._paths_are_safe(canonical):
            return CommandAssessment(
                CommandDecision.DENY,
                canonical,
                "命令包含工作区外的绝对路径",
            )

        if executable in {"curl", "wget"} and any(
            item in {"-o", "--output", "-O", "--output-document"}
            or item.startswith("--output=")
            for item in canonical[1:]
        ):
            return CommandAssessment(
                CommandDecision.DENY,
                canonical,
                "网络命令不能直接写入文件",
            )

        for prefix in ALLOWED_COMMAND_PREFIXES:
            if _has_prefix(canonical, prefix):
                return CommandAssessment(
                    CommandDecision.ALLOW,
                    canonical,
                    "命令位于自动允许列表",
                )

        for prefix in CONFIRM_COMMAND_PREFIXES:
            if _has_prefix(canonical, prefix):
                return CommandAssessment(
                    CommandDecision.CONFIRM,
                    canonical,
                    "命令涉及依赖、网络或远程仓库",
                )

        return CommandAssessment(
            CommandDecision.DENY,
            canonical,
            "命令不在安全策略允许列表中",
        )

    def executable_command(self, canonical: tuple[str, ...]) -> list[str]:
        if canonical[0] == "python":
            return [sys.executable, *canonical[1:]]
        return list(canonical)


ConfirmCallback = Callable[[str], bool]


class SafeCommandRunner:
    def __init__(
        self,
        *,
        root: Path,
        confirm: ConfirmCallback,
    ) -> None:
        self.root = root.resolve()
        self.confirm = confirm
        self.policy = CommandPolicy(self.root)

    def _clip(self, output: str) -> str:
        if len(output) <= MAX_COMMAND_OUTPUT:
            return output or "命令没有输出"
        return "... 前面的输出已截断 ...\n" + output[-MAX_COMMAND_OUTPUT:]

    def run(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> CommandResult:
        if timeout_seconds <= 0:
            raise CommandPolicyError("timeout_seconds 必须大于 0")

        assessment = self.policy.assess(command)

        if assessment.decision is CommandDecision.DENY:
            raise CommandPolicyError(assessment.reason)

        if assessment.decision is CommandDecision.CONFIRM:
            displayed = " ".join(assessment.command)
            if not self.confirm(f"运行需要确认的命令：{displayed}"):
                raise CommandPolicyError("用户拒绝了命令执行")

        executable = self.policy.executable_command(assessment.command)
        started = time.monotonic()
        process = subprocess.Popen(
            executable,
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=sanitized_environment(),
            start_new_session=True,
        )

        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            stdout, stderr = process.communicate()

        exit_code = 124 if timed_out else process.returncode
        output = stdout + stderr

        if timed_out:
            output += "\n命令运行超时，进程组已终止"

        return CommandResult(
            command=tuple(executable),
            exit_code=exit_code,
            timed_out=timed_out,
            elapsed_seconds=round(time.monotonic() - started, 3),
            output=self._clip(output),
            decision=assessment.decision,
        )
