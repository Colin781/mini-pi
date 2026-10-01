import os
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence


MAX_VERIFICATION_OUTPUT = 20_000


class VerificationError(ValueError):
    """验收命令配置不合法。"""


@dataclass(frozen=True, slots=True)
class VerificationResult:
    command: tuple[str, ...]
    exit_code: int
    passed: bool
    timed_out: bool
    elapsed_seconds: float
    output: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def clip_output(
    text: str,
    limit: int = MAX_VERIFICATION_OUTPUT,
) -> str:
    if not text:
        return "命令没有输出"

    if len(text) <= limit:
        return text

    return (
        "... 前面的输出已截断 ...\n"
        + text[-limit:]
    )


def sanitized_environment() -> dict[str, str]:
    environment = os.environ.copy()

    sensitive_fragments = (
        "API_KEY",
        "PASSWORD",
        "SECRET",
        "TOKEN",
    )

    for variable_name in list(environment):
        upper_name = variable_name.upper()

        if any(
            fragment in upper_name
            for fragment in sensitive_fragments
        ):
            environment.pop(variable_name, None)

    return environment


def normalize_verification_command(
    command: Sequence[str],
) -> list[str]:
    if (
        isinstance(command, (str, bytes))
        or not isinstance(command, Sequence)
    ):
        raise VerificationError(
            "command 必须是字符串数组"
        )

    if not command:
        raise VerificationError(
            "command 不能为空"
        )

    if not all(
        isinstance(part, str) and part
        for part in command
    ):
        raise VerificationError(
            "command 中的每一项都必须是非空字符串"
        )

    executable = Path(command[0]).name

    python_names = {
        "python",
        "python3",
        Path(sys.executable).name,
    }

    if executable in python_names:
        if (
            len(command) < 3
            or command[1] != "-m"
        ):
            raise VerificationError(
                "Python 验收命令必须使用 "
                "python -m unittest 或 python -m pytest"
            )

        if command[2] not in {
            "unittest",
            "pytest",
        }:
            raise VerificationError(
                "只允许运行 unittest 或 pytest"
            )

        # -B 用来禁止 Python 读取或生成旧的 pyc 缓存。
        return [
            sys.executable,
            "-B",
            *command[1:],
        ]

    if executable == "pytest":
        return [
            sys.executable,
            "-B",
            "-m",
            "pytest",
            *command[1:],
        ]

    raise VerificationError(
        "验收命令不在允许列表中，"
        "目前只允许 unittest 和 pytest"
    )


def display_command(
    command: Sequence[str],
) -> str:
    return shlex.join(list(command))


def run_verification(
    *,
    root: Path,
    command: Sequence[str],
    timeout_seconds: int,
) -> VerificationResult:
    if timeout_seconds < 1:
        raise VerificationError(
            "timeout_seconds 必须大于 0"
        )

    normalized = normalize_verification_command(
        command
    )

    started = time.monotonic()

    try:
        process = subprocess.run(
            normalized,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=sanitized_environment(),
        )
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout or ""
        stderr = error.stderr or ""

        if isinstance(stdout, bytes):
            stdout = stdout.decode(
                errors="replace"
            )

        if isinstance(stderr, bytes):
            stderr = stderr.decode(
                errors="replace"
            )

        output = clip_output(
            stdout
            + stderr
            + "\n验收命令运行超时"
        )

        return VerificationResult(
            command=tuple(normalized),
            exit_code=124,
            passed=False,
            timed_out=True,
            elapsed_seconds=round(
                time.monotonic() - started,
                3,
            ),
            output=output,
        )

    return VerificationResult(
        command=tuple(normalized),
        exit_code=process.returncode,
        passed=process.returncode == 0,
        timed_out=False,
        elapsed_seconds=round(
            time.monotonic() - started,
            3,
        ),
        output=clip_output(
            process.stdout + process.stderr
        ),
    )
