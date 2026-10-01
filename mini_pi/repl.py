from __future__ import annotations

import argparse
import re
import shlex
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent import run_agent

from mini_pi import __version__
from mini_pi.chat import ChatResult, run_chat
from mini_pi.checkpoints import CheckpointError, CheckpointManager
from mini_pi.reporting import AgentRunReport
from mini_pi.task_scope import TaskScope, TaskScopeError, plan_task_scope
from mini_pi.tools import ToolError, ToolExecutor
from mini_pi.verification import VerificationError, normalize_verification_command


Writer = Callable[[str], None]
AgentRunner = Callable[..., AgentRunReport]
ChatRunner = Callable[..., ChatResult]


AGENT_ACTION_PATTERN = re.compile(
    r"修复|修改|实现|新增|删除|重构|调试|排查|定位|"
    r"运行测试|执行测试|检查代码|分析代码|阅读代码|"
    r"提交代码|推送代码|安装依赖|构建项目|"
    r"\b(?:fix|implement|refactor|debug|test|build|commit)\b",
    re.IGNORECASE,
)

REPOSITORY_PATTERN = re.compile(
    r"(?:^|[\s`'\"])(?:[\w.-]+/)+[\w.-]+|"
    r"\.(?:py|js|ts|tsx|jsx|go|rs|java|kt|rb|php|cs|cpp|c|h|toml|yaml|yml|json|md)\b|"
    r"traceback|stack trace|pytest|unittest|"
    r"这个项目|当前项目|代码库|仓库|测试失败|编译失败|报错",
    re.IGNORECASE,
)

MODEL_QUESTION_PATTERN = re.compile(
    r"(?:你|当前|现在).{0,8}(?:什么|哪个|哪一个).{0,4}模型|"
    r"(?:what|which)\s+model",
    re.IGNORECASE,
)


HELP_TEXT = """可用命令：
  /help                  显示帮助
  /status                显示当前会话状态
  /history               显示本次会话的任务
  /mode [auto|chat|agent] 查看或切换输入模式
  /chat [内容]           强制普通对话；无内容时切换到 chat 模式
  /agent [任务]          强制代码任务；无内容时切换到 agent 模式
  /model [名称]          查看或切换模型
  /verify [命令|off]     查看、设置或关闭自动验收命令
  /diff                  显示当前 Git diff
  /undo                  撤销上一个成功任务的工作区修改
  /new                   清空对话历史并开始新会话
  /clear                 清屏
  /trace                 显示最近一次运行轨迹
  /exit 或 /quit         退出

auto 模式会把普通问题交给轻量对话，把代码任务交给 Agent。
行末输入反斜杠可继续输入下一行。
"""


def classify_input(text: str) -> str:
    if REPOSITORY_PATTERN.search(text) or AGENT_ACTION_PATTERN.search(text):
        return "agent"
    return "chat"


@dataclass(slots=True)
class ReplConfig:
    workspace: Path
    model: str = "deepseek-flash"
    max_steps: int = 15
    max_context_chars: int = 30_000
    max_file_chars: int = 10_000
    max_repairs: int = 2
    command_timeout: int = 60
    verify_command: tuple[str, ...] | None = None
    protected_paths: list[str] = field(default_factory=list)
    allowed_changes: list[str] = field(default_factory=list)
    yes: bool = False
    conversation_chars: int = 12_000
    history_file: Path | None = None
    mode: str = "auto"

    def __post_init__(self) -> None:
        self.workspace = self.workspace.expanduser().resolve()
        if self.history_file is not None:
            self.history_file = self.history_file.expanduser().resolve()
        if self.conversation_chars < 1_000:
            raise ValueError("conversation_chars 不能小于 1000")
        if self.mode not in {"auto", "chat", "agent"}:
            raise ValueError("mode 必须是 auto、chat 或 agent")


@dataclass(slots=True)
class ReplTurn:
    task: str
    checkpoint_id: str
    history_messages: tuple[dict[str, str], dict[str, str]]
    report: AgentRunReport


class TerminalInput:
    """优先使用 prompt_toolkit，未安装时退回 input。"""

    def __init__(self, history_file: Path | None) -> None:
        self.history_file = history_file
        self.session: Any | None = None
        self.readline: Any | None = None

        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.history import FileHistory

            history = None
            if history_file is not None:
                history_file.parent.mkdir(parents=True, exist_ok=True)
                history = FileHistory(str(history_file))
            self.session = PromptSession(history=history)
            return
        except ImportError:
            pass

        try:
            import readline

            self.readline = readline
            if history_file is not None and history_file.exists():
                try:
                    readline.read_history_file(history_file)
                except OSError:
                    pass
        except ImportError:
            self.readline = None

    def __call__(self, prompt: str) -> str:
        if self.session is not None:
            return str(self.session.prompt(prompt))
        return input(prompt)

    def close(self) -> None:
        if self.readline is None or self.history_file is None:
            return
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.readline.write_history_file(self.history_file)
        except OSError:
            pass


class InteractiveRepl:
    def __init__(
        self,
        config: ReplConfig,
        *,
        reader: Callable[[str], str] | None = None,
        writer: Writer = print,
        agent_runner: AgentRunner = run_agent,
        chat_runner: ChatRunner = run_chat,
    ) -> None:
        self.config = config
        self.writer = writer
        self.agent_runner = agent_runner
        self.chat_runner = chat_runner
        self.terminal_input: TerminalInput | None = None

        if reader is None:
            self.terminal_input = TerminalInput(config.history_file)
            self.reader = self.terminal_input
        else:
            self.reader = reader

        self.history: list[dict[str, str]] = []
        self.turns: list[ReplTurn] = []
        self.last_report: AgentRunReport | None = None
        self.checkpoints = CheckpointManager(config.workspace)
        self.running = True

    def run(self) -> int:
        if not self.config.workspace.is_dir():
            self.writer(f"工作目录不存在：{self.config.workspace}")
            self.close()
            return 2

        self.writer(f"Mini Pi v{__version__}")
        self.writer(f"工作目录：{self.config.workspace}")
        self.writer("输入 /help 查看命令，Ctrl+D 退出。")

        try:
            while self.running:
                try:
                    line = self._read_task()
                except KeyboardInterrupt:
                    self.writer("\n已取消当前输入。")
                    continue
                except EOFError:
                    self.writer("")
                    break

                if not line.strip():
                    continue

                self.handle_line(line)
        finally:
            self.close()

        return 0

    def close(self) -> None:
        self.checkpoints.cleanup()
        if self.terminal_input is not None:
            self.terminal_input.close()

    def _read_task(self) -> str:
        lines: list[str] = []
        prompt = "mini-pi> "

        while True:
            line = self.reader(prompt)
            if line.endswith("\\"):
                lines.append(line[:-1])
                prompt = "      ... "
                continue
            lines.append(line)
            return "\n".join(lines)

    def handle_line(self, line: str) -> None:
        stripped = line.strip()
        if stripped.startswith("/"):
            self._handle_command(stripped)
            return
        self._dispatch_input(stripped)

    def _handle_command(self, line: str) -> None:
        command, _, argument = line.partition(" ")
        command = command.casefold()
        argument = argument.strip()

        handlers: dict[str, Callable[[str], None]] = {
            "/help": self._command_help,
            "/status": self._command_status,
            "/history": self._command_history,
            "/mode": self._command_mode,
            "/chat": self._command_chat,
            "/agent": self._command_agent,
            "/model": self._command_model,
            "/verify": self._command_verify,
            "/diff": self._command_diff,
            "/undo": self._command_undo,
            "/new": self._command_new,
            "/clear": self._command_clear,
            "/trace": self._command_trace,
            "/exit": self._command_exit,
            "/quit": self._command_exit,
        }

        handler = handlers.get(command)
        if handler is None:
            self.writer(f"未知命令：{command}。输入 /help 查看帮助。")
            return
        handler(argument)

    def _command_help(self, _: str) -> None:
        self.writer(HELP_TEXT.rstrip())

    def _command_status(self, _: str) -> None:
        verify = (
            shlex.join(self.config.verify_command)
            if self.config.verify_command
            else "未设置"
        )
        self.writer(f"工作目录：{self.config.workspace}")
        self.writer(f"模型：{self.config.model}")
        self.writer(f"输入模式：{self.config.mode}")
        self.writer(f"自动验收：{verify}")
        self.writer(
            "会话轮数："
            f"{sum(1 for item in self.history if item['role'] == 'user')}"
        )
        self.writer(f"对话上下文字符数：{self._history_chars()}")
        if self.last_report is not None:
            self.writer(
                "最近运行："
                f"{self.last_report.status}，"
                f"{self.last_report.elapsed_seconds:.3f} 秒"
            )

    def _command_history(self, _: str) -> None:
        user_messages = [
            item for item in self.history if item["role"] == "user"
        ]
        if not user_messages:
            self.writer("当前会话还没有记录。")
            return
        for index, item in enumerate(user_messages, start=1):
            mode = item.get("mode", "agent")
            self.writer(f"{index}. [{mode}] {item['content']}")

    def _command_mode(self, argument: str) -> None:
        if not argument:
            self.writer(f"当前输入模式：{self.config.mode}")
            return
        mode = argument.casefold()
        if mode not in {"auto", "chat", "agent"}:
            self.writer("模式必须是 auto、chat 或 agent。")
            return
        self.config.mode = mode
        self.writer(f"已切换输入模式：{mode}")

    def _command_chat(self, argument: str) -> None:
        if not argument:
            self.config.mode = "chat"
            self.writer("已切换输入模式：chat")
            return
        self._run_chat(argument)

    def _command_agent(self, argument: str) -> None:
        if not argument:
            self.config.mode = "agent"
            self.writer("已切换输入模式：agent")
            return
        self._run_agent_task(argument)

    def _command_model(self, argument: str) -> None:
        if not argument:
            self.writer(f"当前模型：{self.config.model}")
            return
        self.config.model = argument
        self.writer(f"已切换模型：{argument}")

    def _command_verify(self, argument: str) -> None:
        if not argument:
            current = (
                shlex.join(self.config.verify_command)
                if self.config.verify_command
                else "未设置"
            )
            self.writer(f"当前自动验收：{current}")
            return
        if argument.casefold() in {"off", "none"}:
            self.config.verify_command = None
            self.writer("已关闭自动验收。")
            return
        try:
            parsed = tuple(shlex.split(argument))
        except ValueError as error:
            self.writer(f"命令解析失败：{error}")
            return
        if not parsed:
            self.writer("验收命令不能为空。")
            return
        try:
            self.config.verify_command = tuple(
                normalize_verification_command(parsed)
            )
        except VerificationError as error:
            self.writer(f"验收命令不可用：{error}")
            return
        self.writer(f"已设置自动验收：{shlex.join(parsed)}")

    def _command_diff(self, _: str) -> None:
        try:
            executor = ToolExecutor(
                root=self.config.workspace,
                confirm=lambda _: False,
            )
            self.writer(executor.git_diff())
        except (OSError, ToolError, ValueError) as error:
            self.writer(f"读取 diff 失败：{error}")

    def _command_undo(self, _: str) -> None:
        if not self.turns:
            self.writer("没有可以撤销的任务。")
            return
        turn = self.turns.pop()
        try:
            self.checkpoints.restore(turn.checkpoint_id)
        except (CheckpointError, OSError) as error:
            self.turns.append(turn)
            self.writer(f"撤销失败：{error}")
            return
        self.checkpoints.discard(turn.checkpoint_id)
        removed = {id(item) for item in turn.history_messages}
        self.history[:] = [
            item for item in self.history if id(item) not in removed
        ]
        self.last_report = self.turns[-1].report if self.turns else None
        self.writer(f"已撤销：{turn.task}")

    def _command_new(self, _: str) -> None:
        self.checkpoints.cleanup()
        self.checkpoints = CheckpointManager(self.config.workspace)
        self.history.clear()
        self.turns.clear()
        self.last_report = None
        self.writer("已开始新会话，工作区文件保持当前状态。")

    def _command_clear(self, _: str) -> None:
        if sys.stdout.isatty():
            self.writer("\033[2J\033[H")

    def _command_trace(self, _: str) -> None:
        if self.last_report is None or not self.last_report.trace_path:
            self.writer("当前没有运行轨迹。")
            return
        self.writer(f"最近运行轨迹：{self.last_report.trace_path}")

    def _command_exit(self, _: str) -> None:
        self.running = False

    def _dispatch_input(self, text: str) -> None:
        mode = self.config.mode
        if mode == "auto":
            mode = classify_input(text)
            if (
                mode == "chat"
                and self.history
                and self.history[-1].get("mode") == "agent"
                and text.startswith(("继续", "再", "然后", "接着"))
            ):
                mode = "agent"

        if mode == "agent":
            self._run_agent_task(text)
        else:
            self._run_chat(text)

    def _run_chat(self, message: str) -> None:
        if MODEL_QUESTION_PATTERN.search(message):
            answer = (
                "我是 Mini Pi 终端助手，当前配置的模型标识是 "
                f"{self.config.model}。"
            )
            self.writer(answer)
            self._append_history(message, answer, "chat")
            return

        try:
            result = self.chat_runner(
                message,
                model=self.config.model,
                conversation_history=self._bounded_history(),
            )
        except KeyboardInterrupt:
            self.writer("\n已取消当前对话。")
            return
        except Exception as error:
            self.writer(f"对话失败：{error}")
            return

        if result.status != "completed" or result.answer is None:
            self.writer(f"对话失败：{result.error or result.status}")
            return

        self.writer(result.answer)
        self._append_history(message, result.answer, "chat")

    def _run_agent_task(self, task: str) -> None:
        try:
            scope = plan_task_scope(self.config.workspace, task)
        except TaskScopeError as error:
            self.writer(f"任务路径无效：{error}")
            return

        checkpoint = self.checkpoints.create(f"repl:{len(self.turns) + 1}")
        report_path, trace_path = self._run_paths()
        args = self._build_agent_args(scope, report_path, trace_path)

        try:
            report = self.agent_runner(
                args,
                conversation_history=self._bounded_history(),
            )
        except KeyboardInterrupt:
            self.checkpoints.restore(checkpoint.checkpoint_id)
            self.checkpoints.discard(checkpoint.checkpoint_id)
            self.writer("\n任务已取消，工作区已恢复。")
            return
        except Exception as error:
            self.checkpoints.restore(checkpoint.checkpoint_id)
            self.checkpoints.discard(checkpoint.checkpoint_id)
            self.writer(f"任务异常，工作区已恢复：{error}")
            return

        self.last_report = report
        report.write_json(report_path)

        if report.status in {"success", "completed"}:
            answer = report.final_answer or "任务已完成。"
            history_messages = self._append_history(task, answer, "agent")
            self.turns.append(
                ReplTurn(
                    task=task,
                    checkpoint_id=checkpoint.checkpoint_id,
                    history_messages=history_messages,
                    report=report,
                )
            )
        else:
            try:
                self.checkpoints.restore(checkpoint.checkpoint_id)
            except (CheckpointError, OSError) as error:
                self.writer(f"失败任务的工作区恢复失败：{error}")
            self.checkpoints.discard(checkpoint.checkpoint_id)

        self.writer(
            f"运行结束：{report.status} | "
            f"{report.rounds} 轮 | {report.tool_calls} 次工具调用 | "
            f"{report.elapsed_seconds:.3f} 秒"
        )
        self.writer(f"报告：{report_path}")

    def _build_agent_args(
        self,
        scope: TaskScope,
        report_path: Path,
        trace_path: Path,
    ) -> argparse.Namespace:
        protected_paths = list(self.config.protected_paths)
        if (
            scope.protected_test_path
            and scope.protected_test_path not in protected_paths
        ):
            protected_paths.append(scope.protected_test_path)

        return argparse.Namespace(
            task=scope.task,
            workspace=scope.workspace,
            model=self.config.model,
            max_steps=self.config.max_steps,
            max_context_chars=self.config.max_context_chars,
            max_file_chars=self.config.max_file_chars,
            max_repairs=self.config.max_repairs,
            verify_command=(
                self.config.verify_command or scope.verify_command
            ),
            command_timeout=self.config.command_timeout,
            protected_path=protected_paths,
            allowed_change=list(self.config.allowed_changes),
            yes=self.config.yes,
            report=report_path,
            trace=trace_path,
            show_diff=False,
        )

    def _run_paths(self) -> tuple[Path, Path]:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_id = f"{timestamp}-{uuid.uuid4().hex[:8]}"
        run_directory = self.config.workspace / ".mini-pi" / "runs"
        return (
            run_directory / f"{run_id}.json",
            run_directory / f"{run_id}.jsonl",
        )

    def _history_chars(self) -> int:
        return sum(len(item["content"]) for item in self.history)

    def _append_history(
        self,
        user_content: str,
        assistant_content: str,
        mode: str,
    ) -> tuple[dict[str, str], dict[str, str]]:
        user_message = {
            "role": "user",
            "content": user_content,
            "mode": mode,
        }
        assistant_message = {
            "role": "assistant",
            "content": assistant_content[:4_000],
            "mode": mode,
        }
        self.history.extend([user_message, assistant_message])
        return user_message, assistant_message

    def _bounded_history(self) -> list[dict[str, str]]:
        selected: list[dict[str, str]] = []
        used = 0

        for message in reversed(self.history):
            content = message["content"]
            if used + len(content) > self.config.conversation_chars:
                break
            selected.append(
                {
                    "role": message["role"],
                    "content": content,
                }
            )
            used += len(content)

        selected.reverse()
        if selected and selected[0]["role"] == "assistant":
            selected.pop(0)
        return selected
