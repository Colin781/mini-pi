from __future__ import annotations

import argparse
import uuid
from pathlib import Path

from dotenv import load_dotenv

from agent import parse_command_text, run_agent
from mini_pi import __version__
from mini_pi.config import AppConfig, ConfigError, load_config
from mini_pi.repl import InteractiveRepl, ReplConfig
from mini_pi.sessions import SessionError, SessionStore
from mini_pi.terminal_ui import TerminalUI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mini-pi",
        description="基于 DeepSeek 的交互式 Coding Agent",
    )
    parser.add_argument(
        "task",
        nargs="?",
        help="单次运行的任务；省略后进入交互模式",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="Agent 工作目录，默认是当前目录",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="DeepSeek 模型名称",
    )
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--max-context-chars", type=int)
    parser.add_argument("--max-file-chars", type=int)
    parser.add_argument("--max-repairs", type=int)
    parser.add_argument("--command-timeout", type=int)
    parser.add_argument("--verify-command", type=parse_command_text)
    parser.add_argument("--protected-path", action="append")
    parser.add_argument("--allowed-change", action="append")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--trace", type=Path)
    parser.add_argument(
        "--conversation-chars",
        type=int,
        default=None,
        help="跨任务对话历史字符预算",
    )
    parser.add_argument(
        "--mode",
        choices=("auto", "chat", "agent"),
        default=None,
        help="REPL 输入模式，默认自动区分普通对话和代码任务",
    )
    parser.add_argument(
        "--verbosity",
        choices=("quiet", "normal", "verbose"),
        default=None,
        help="终端输出详细程度",
    )
    session_group = parser.add_mutually_exclusive_group()
    session_group.add_argument(
        "--resume",
        metavar="SESSION_ID",
        help="恢复指定会话",
    )
    session_group.add_argument(
        "--continue",
        dest="continue_session",
        action="store_true",
        help="恢复当前工作区最近使用的会话",
    )
    session_group.add_argument(
        "--no-session",
        action="store_true",
        help="本次运行不保存 REPL 会话",
    )
    parser.add_argument(
        "--session-root",
        type=Path,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--no-history",
        action="store_true",
        help="不保存终端输入历史",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"Mini Pi {__version__}",
    )
    return parser


def _load_environment() -> None:
    load_dotenv(Path.cwd() / ".env")
    load_dotenv(Path.home() / ".config" / "mini-pi" / ".env")


def _value(value, fallback):
    return fallback if value is None else value


def _apply_agent_config(args: argparse.Namespace, config: AppConfig) -> None:
    args.model = _value(args.model, config.model.name)
    args.max_steps = _value(args.max_steps, config.agent.max_steps)
    args.max_repairs = _value(args.max_repairs, config.agent.max_repairs)
    args.max_context_chars = _value(
        args.max_context_chars,
        config.agent.max_context_chars,
    )
    args.max_file_chars = _value(
        args.max_file_chars,
        config.agent.max_file_chars,
    )
    args.command_timeout = _value(
        args.command_timeout,
        config.agent.command_timeout,
    )
    args.protected_path = _value(
        args.protected_path,
        list(config.safety.protected_paths),
    )
    args.allowed_change = _value(
        args.allowed_change,
        list(config.safety.allowed_changes),
    )
    args.verbosity = _value(args.verbosity, config.repl.verbosity)
    args.mode = _value(args.mode, config.repl.mode)
    args.conversation_chars = _value(
        args.conversation_chars,
        config.repl.conversation_chars,
    )


def _config_from_args(
    args: argparse.Namespace,
    app_config: AppConfig,
    *,
    session_store: SessionStore | None,
    session_id: str | None,
) -> ReplConfig:
    history_file = None
    if not args.no_history:
        history_file = Path.home() / ".mini-pi" / "history"
    return ReplConfig(
        workspace=args.workspace,
        model=args.model,
        max_steps=args.max_steps,
        max_context_chars=args.max_context_chars,
        max_file_chars=args.max_file_chars,
        max_repairs=args.max_repairs,
        command_timeout=args.command_timeout,
        verify_command=args.verify_command,
        protected_paths=args.protected_path,
        allowed_changes=args.allowed_change,
        yes=args.yes,
        conversation_chars=args.conversation_chars,
        history_file=history_file,
        mode=args.mode,
        verbosity=args.verbosity,
        session_store=session_store,
        session_id=session_id,
        app_config=app_config,
    )


def _one_shot(args: argparse.Namespace) -> int:
    if args.trace is None:
        if args.report is not None:
            args.trace = args.report.with_suffix(".trace.jsonl")
        else:
            args.trace = (
                Path.home()
                / ".mini-pi"
                / "traces"
                / f"{uuid.uuid4().hex}.jsonl"
            )
    report = run_agent(args)
    if args.report is not None:
        report.write_json(args.report)
    TerminalUI().run_report(
        report,
        report_path=args.report.resolve() if args.report else None,
        include_answer=False,
    )
    return report.exit_code


def main(argv: list[str] | None = None) -> int:
    _load_environment()
    args = build_parser().parse_args(argv)
    args.workspace = args.workspace.expanduser().resolve()

    try:
        session_store_candidate = SessionStore(
            args.session_root
            or (Path.home() / ".mini-pi" / "sessions")
        )
        session_id = None
        if args.resume:
            resumed = session_store_candidate.load(args.resume)
            args.workspace = Path(resumed.workspace).expanduser().resolve()
            session_id = resumed.session_id
        elif args.continue_session:
            latest = session_store_candidate.latest_for_workspace(
                args.workspace
            )
            if latest is None:
                raise SessionError("当前工作区没有可恢复的会话")
            session_id = latest.session_id

        app_config = load_config(args.workspace)
        _apply_agent_config(args, app_config)
    except (ConfigError, SessionError) as error:
        print(f"配置错误：{error}")
        return 2

    if args.task:
        return _one_shot(args)

    try:
        persist_sessions = (
            (
                app_config.repl.persist_sessions
                or bool(args.resume)
                or args.continue_session
            )
            and not args.no_session
        )
        session_store = (
            session_store_candidate if persist_sessions else None
        )

        config = _config_from_args(
            args,
            app_config,
            session_store=session_store,
            session_id=session_id,
        )
    except (ValueError, SessionError) as error:
        print(f"配置错误：{error}")
        return 2
    try:
        return InteractiveRepl(config).run()
    except (OSError, SessionError) as error:
        print(f"会话初始化失败：{error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
