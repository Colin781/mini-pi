from __future__ import annotations

import argparse
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

from agent import parse_command_text, run_agent
from mini_pi import __version__
from mini_pi.repl import InteractiveRepl, ReplConfig


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
        default=os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
        help="DeepSeek 模型名称",
    )
    parser.add_argument("--max-steps", type=int, default=15)
    parser.add_argument("--max-context-chars", type=int, default=30_000)
    parser.add_argument("--max-file-chars", type=int, default=10_000)
    parser.add_argument("--max-repairs", type=int, default=2)
    parser.add_argument("--command-timeout", type=int, default=60)
    parser.add_argument("--verify-command", type=parse_command_text)
    parser.add_argument("--protected-path", action="append", default=[])
    parser.add_argument("--allowed-change", action="append", default=[])
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--trace", type=Path)
    parser.add_argument(
        "--conversation-chars",
        type=int,
        default=12_000,
        help="跨任务对话历史字符预算",
    )
    parser.add_argument(
        "--mode",
        choices=("auto", "chat", "agent"),
        default="auto",
        help="REPL 输入模式，默认自动区分普通对话和代码任务",
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


def _config_from_args(args: argparse.Namespace) -> ReplConfig:
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
    if report.error:
        print(f"错误：{report.error}")
    print(
        f"状态：{report.status} | {report.rounds} 轮 | "
        f"{report.tool_calls} 次工具调用 | "
        f"{report.elapsed_seconds:.3f} 秒"
    )
    if report.trace_path:
        print(f"运行轨迹：{report.trace_path}")
    return report.exit_code


def main(argv: list[str] | None = None) -> int:
    _load_environment()
    args = build_parser().parse_args(argv)
    args.workspace = args.workspace.expanduser().resolve()

    if args.task:
        return _one_shot(args)

    try:
        config = _config_from_args(args)
    except ValueError as error:
        print(f"配置错误：{error}")
        return 2
    return InteractiveRepl(config).run()


if __name__ == "__main__":
    raise SystemExit(main())
