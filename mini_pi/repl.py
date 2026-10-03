from __future__ import annotations

import argparse
import json
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
from mini_pi.config import AppConfig
from mini_pi.conversation import build_conversation_window
from mini_pi.reporting import AgentRunReport
from mini_pi.sessions import SessionError, SessionMetadata, SessionStore
from mini_pi.task_scope import TaskScope, TaskScopeError, plan_task_scope
from mini_pi.terminal_ui import TerminalUI
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
  /sessions              列出已保存会话
  /resume ID             恢复指定会话
  /rename 名称           重命名当前会话
  /compact               压缩较早的对话上下文
  /context               显示当前上下文使用情况
  /config                显示合并后的有效配置
  /mode [auto|chat|agent] 查看或切换输入模式
  /chat [内容]           强制普通对话；无内容时切换到 chat 模式
  /agent [任务]          强制代码任务；无内容时切换到 agent 模式
  /model [名称]          查看或切换模型
  /verify [命令|off]     查看、设置或关闭自动验收命令
  /changes               显示最近任务修改的位置
  /diff [文件|序号]      显示全部或单个文件的 Git diff
  /undo                  撤销上一个成功任务的工作区修改
  /new [名称]            创建并切换到新会话
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
    verbosity: str = "normal"
    session_store: SessionStore | None = None
    session_id: str | None = None
    app_config: AppConfig | None = None

    def __post_init__(self) -> None:
        self.workspace = self.workspace.expanduser().resolve()
        if self.history_file is not None:
            self.history_file = self.history_file.expanduser().resolve()
        if self.conversation_chars < 1_000:
            raise ValueError("conversation_chars 不能小于 1000")
        if self.mode not in {"auto", "chat", "agent"}:
            raise ValueError("mode 必须是 auto、chat 或 agent")
        if self.verbosity not in {"quiet", "normal", "verbose"}:
            raise ValueError("verbosity 必须是 quiet、normal 或 verbose")


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
        self.ui = TerminalUI(writer)
        self.agent_runner = agent_runner
        self.chat_runner = chat_runner
        self.terminal_input: TerminalInput | None = None
        self.session_store = config.session_store
        self.session: SessionMetadata | None = None
        self.conversation_summary = ""
        self.summarized_message_count = 0

        if reader is None:
            self.terminal_input = TerminalInput(config.history_file)
            self.reader = self.terminal_input
        else:
            self.reader = reader

        self.history: list[dict[str, str]] = []
        if self.session_store is not None:
            if config.session_id:
                self._load_session(config.session_id)
            else:
                self._create_session()
        self.turns: list[ReplTurn] = []
        self.last_report: AgentRunReport | None = None
        self.checkpoints = CheckpointManager(config.workspace)
        self.running = True

    def run(self) -> int:
        if not self.config.workspace.is_dir():
            self.writer(f"工作目录不存在：{self.config.workspace}")
            self.close()
            return 2

        self.ui.welcome(
            version=f"v{__version__}",
            workspace=self.config.workspace,
            model=self.config.model,
            mode=self.config.mode,
            session=(
                f"{self.session.name} ({self.session.session_id})"
                if self.session is not None
                else None
            ),
        )

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
        self._persist_session_settings()
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
            "/sessions": self._command_sessions,
            "/resume": self._command_resume,
            "/rename": self._command_rename,
            "/compact": self._command_compact,
            "/context": self._command_context,
            "/config": self._command_config,
            "/mode": self._command_mode,
            "/chat": self._command_chat,
            "/agent": self._command_agent,
            "/model": self._command_model,
            "/verify": self._command_verify,
            "/changes": self._command_changes,
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
        rows: list[tuple[str, object]] = [
            ("工作区", self.config.workspace),
            ("模型", self.config.model),
            ("模式", self.config.mode),
            ("输出", self.config.verbosity),
            ("自动验收", verify),
            (
                "会话轮数",
                sum(1 for item in self.history if item["role"] == "user"),
            ),
            ("上下文", f"{self._history_chars()} 字符"),
            ("摘要", f"{len(self.conversation_summary)} 字符"),
        ]
        if self.session is not None:
            rows.insert(
                4,
                ("会话", f"{self.session.name} ({self.session.session_id})"),
            )
        if self.last_report is not None:
            rows.append(
                (
                    "最近运行",
                    f"{self.last_report.status} · "
                    f"{self.last_report.elapsed_seconds:.2f}s",
                )
            )
        self.ui.key_values("当前状态", rows)

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

    def _command_sessions(self, _: str) -> None:
        if self.session_store is None:
            self.writer("当前已关闭会话持久化。")
            return
        sessions = self.session_store.list()
        if not sessions:
            self.writer("没有已保存会话。")
            return
        for item in sessions:
            marker = "*" if self.session and item.session_id == self.session.session_id else " "
            self.writer(
                f"{marker} {item.session_id}  {item.name}  "
                f"[{item.mode}]  {item.workspace}"
            )

    def _command_resume(self, argument: str) -> None:
        if self.session_store is None:
            self.writer("当前已关闭会话持久化。")
            return
        if not argument:
            self.writer("用法：/resume SESSION_ID")
            return
        try:
            self._switch_session(argument)
        except SessionError as error:
            self.writer(f"恢复会话失败：{error}")
            return
        self.writer(
            f"已恢复会话：{self.session.name} ({self.session.session_id})"
        )

    def _command_rename(self, argument: str) -> None:
        if self.session_store is None or self.session is None:
            self.writer("当前已关闭会话持久化。")
            return
        if not argument:
            self.writer("会话名称不能为空。")
            return
        try:
            self.session = self.session_store.update(
                self.session,
                name=argument,
            )
        except (OSError, SessionError) as error:
            self.writer(f"重命名会话失败：{error}")
            return
        self.writer(f"会话已重命名：{argument}")

    def _command_compact(self, _: str) -> None:
        window = build_conversation_window(
            self.history,
            max_chars=self.config.conversation_chars,
            summary=self.conversation_summary,
            summarized_message_count=self.summarized_message_count,
            force=True,
        )
        self._save_compaction(window.summary, window.summarized_message_count)
        self.writer(
            "上下文已压缩："
            f"摘要 {len(window.summary)} 字符，"
            f"保留 {len(window.messages)} 条上下文消息。"
        )

    def _command_context(self, _: str) -> None:
        window = self._conversation_window()
        self.writer(f"上下文预算：{self.config.conversation_chars}")
        self.writer(f"历史消息：{len(self.history)}")
        self.writer(f"已摘要消息：{window.summarized_message_count}")
        self.writer(f"摘要字符数：{len(window.summary)}")
        self.writer(f"本轮上下文字符数：{window.context_chars}")

    def _command_config(self, _: str) -> None:
        if self.config.app_config is None:
            self.writer("当前没有加载 TOML 配置。")
            return
        self.writer(
            json.dumps(
                self.config.app_config.to_dict(),
                ensure_ascii=False,
                indent=2,
            )
        )

    def _command_mode(self, argument: str) -> None:
        if not argument:
            self.writer(f"当前输入模式：{self.config.mode}")
            return
        mode = argument.casefold()
        if mode not in {"auto", "chat", "agent"}:
            self.writer("模式必须是 auto、chat 或 agent。")
            return
        self.config.mode = mode
        self._persist_session_settings()
        self.writer(f"已切换输入模式：{mode}")

    def _command_chat(self, argument: str) -> None:
        if not argument:
            self.config.mode = "chat"
            self._persist_session_settings()
            self.writer("已切换输入模式：chat")
            return
        self._run_chat(argument)

    def _command_agent(self, argument: str) -> None:
        if not argument:
            self.config.mode = "agent"
            self._persist_session_settings()
            self.writer("已切换输入模式：agent")
            return
        self._run_agent_task(argument)

    def _command_model(self, argument: str) -> None:
        if not argument:
            self.writer(f"当前模型：{self.config.model}")
            return
        self.config.model = argument
        self._persist_session_settings()
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

    def _command_changes(self, _: str) -> None:
        if self.last_report is None:
            self.writer("还没有 Agent 任务记录。")
            return
        self.ui.title("最近修改", icon="Δ")
        self.ui.render_changes(self.last_report.change_details)

    def _command_diff(self, argument: str) -> None:
        try:
            executor = ToolExecutor(
                root=self.config.workspace,
                confirm=lambda _: False,
            )
            path = self._resolve_change_argument(argument)
            self.ui.diff(executor.git_diff(path), path=path)
        except (OSError, ToolError, ValueError) as error:
            self.writer(f"读取 diff 失败：{error}")

    def _resolve_change_argument(self, argument: str) -> str | None:
        if not argument:
            return None
        if argument.isdigit() and self.last_report is not None:
            index = int(argument) - 1
            if index < 0 or index >= len(self.last_report.changed_files):
                raise ValueError("变更序号超出范围")
            return self.last_report.changed_files[index]
        return argument

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
        removed_ids = [
            item.get("message_id", "")
            for item in turn.history_messages
            if item.get("message_id")
        ]
        self.history[:] = [
            item for item in self.history if id(item) not in removed
        ]
        if self.session_store is not None and self.session is not None:
            self.session_store.remove_messages(
                self.session.session_id,
                removed_ids,
            )
            self._save_compaction("", 0)
        self.last_report = self.turns[-1].report if self.turns else None
        self.writer(f"已撤销：{turn.task}")

    def _command_new(self, argument: str) -> None:
        self.checkpoints.cleanup()
        self.checkpoints = CheckpointManager(self.config.workspace)
        self.history.clear()
        self.turns.clear()
        self.last_report = None
        self.conversation_summary = ""
        self.summarized_message_count = 0
        if self.session_store is not None:
            self._create_session(name=argument or None)
        self.writer(
            "已开始新会话，工作区文件保持当前状态。"
            + (
                f" 会话 ID：{self.session.session_id}"
                if self.session is not None
                else ""
            )
        )

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
        self.ui.task_started(scope.task, scope.workspace)

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
        if self.session_store is not None and self.session is not None:
            try:
                self.session_store.append_run(
                    self.session.session_id,
                    report.to_dict(),
                )
            except (OSError, SessionError) as error:
                self._disable_session_persistence(error)

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

        self.ui.run_report(report, report_path=report_path)

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
            verbosity=self.config.verbosity,
            output_writer=self.writer,
            embedded=True,
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
        if self.session_store is not None and self.session is not None:
            try:
                user_message = self.session_store.append_message(
                    self.session.session_id,
                    role="user",
                    content=user_content,
                    mode=mode,
                )
                assistant_message = self.session_store.append_message(
                    self.session.session_id,
                    role="assistant",
                    content=assistant_content[:4_000],
                    mode=mode,
                )
                self.session = self.session_store.load(
                    self.session.session_id
                )
            except (OSError, SessionError) as error:
                self._disable_session_persistence(error)
                user_message = {}
                assistant_message = {}
        else:
            user_message = {}
            assistant_message = {}

        if not user_message or not assistant_message:
            user_message = {
                "message_id": uuid.uuid4().hex,
                "role": "user",
                "content": user_content,
                "mode": mode,
            }
            assistant_message = {
                "message_id": uuid.uuid4().hex,
                "role": "assistant",
                "content": assistant_content[:4_000],
                "mode": mode,
            }
        self.history.extend([user_message, assistant_message])
        self._auto_compact()
        return user_message, assistant_message

    def _bounded_history(self) -> list[dict[str, str]]:
        return self._conversation_window().messages

    def _conversation_window(self):
        window = build_conversation_window(
            self.history,
            max_chars=self.config.conversation_chars,
            summary=self.conversation_summary,
            summarized_message_count=self.summarized_message_count,
        )
        if (
            window.summary != self.conversation_summary
            or window.summarized_message_count
            != self.summarized_message_count
        ):
            self._save_compaction(
                window.summary,
                window.summarized_message_count,
            )
        return window

    def _auto_compact(self) -> None:
        if self._history_chars() + len(self.conversation_summary) <= (
            self.config.conversation_chars
        ):
            return
        self._conversation_window()

    def _save_compaction(self, summary: str, count: int) -> None:
        self.conversation_summary = summary
        self.summarized_message_count = count
        if self.session_store is not None and self.session is not None:
            try:
                self.session = self.session_store.update(
                    self.session,
                    summary=summary,
                    summarized_message_count=count,
                )
            except (OSError, SessionError) as error:
                self._disable_session_persistence(error)

    def _create_session(self, name: str | None = None) -> None:
        if self.session_store is None:
            return
        self.session = self.session_store.create(
            workspace=self.config.workspace,
            model=self.config.model,
            mode=self.config.mode,
            name=name,
        )
        self.config.session_id = self.session.session_id

    def _load_session(self, session_id: str) -> None:
        if self.session_store is None:
            raise SessionError("未配置会话存储")
        metadata = self.session_store.load(session_id)
        workspace = Path(metadata.workspace).expanduser().resolve()
        if not workspace.is_dir():
            raise SessionError(f"会话工作目录不存在：{workspace}")
        self.session = metadata
        self.config.session_id = metadata.session_id
        self.config.workspace = workspace
        self.config.model = metadata.model
        self.config.mode = metadata.mode
        self.history = self.session_store.load_messages(session_id)
        self.conversation_summary = metadata.summary
        self.summarized_message_count = metadata.summarized_message_count

    def _switch_session(self, session_id: str) -> None:
        self._persist_session_settings()
        self.checkpoints.cleanup()
        self._load_session(session_id)
        self.checkpoints = CheckpointManager(self.config.workspace)
        self.turns.clear()
        self.last_report = None

    def _persist_session_settings(self) -> None:
        if self.session_store is None or self.session is None:
            return
        try:
            self.session = self.session_store.update(
                self.session,
                workspace=str(self.config.workspace),
                model=self.config.model,
                mode=self.config.mode,
                summary=self.conversation_summary,
                summarized_message_count=self.summarized_message_count,
            )
        except (OSError, SessionError) as error:
            self._disable_session_persistence(error)

    def _disable_session_persistence(self, error: Exception) -> None:
        self.writer(f"会话保存失败，已改为仅保存在内存中：{error}")
        self.session_store = None
        self.session = None
