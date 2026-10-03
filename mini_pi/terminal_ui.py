from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mini_pi.reporting import AgentRunReport


Writer = Callable[[str], None]

STATUS_LABELS = {
    "success": ("✓", "完成"),
    "completed": ("✓", "完成"),
    "cancelled": ("!", "已取消"),
    "verification_failed": ("✗", "验收失败"),
    "invalid_solution": ("✗", "修改无效"),
    "api_error": ("✗", "接口错误"),
    "configuration_error": ("✗", "配置错误"),
    "max_steps_reached": ("!", "达到轮数上限"),
}

TOOL_LABELS = {
    "repository_summary": "读取仓库概览",
    "list_files": "列出文件",
    "search_text": "搜索文本",
    "find_symbol": "查找符号",
    "find_references": "查找引用",
    "read_file": "读取文件",
    "replace_text": "修改文件",
    "apply_patch": "应用补丁",
    "create_checkpoint": "创建检查点",
    "restore_checkpoint": "恢复检查点",
    "run_command": "运行命令",
    "git_diff": "检查变更",
    "verification": "运行验收",
}


class TerminalUI:
    """无额外依赖的终端渲染器；TTY 中使用颜色，重定向时保持纯文本。"""

    def __init__(
        self,
        writer: Writer = print,
        *,
        color: bool | None = None,
    ) -> None:
        self.writer = writer
        if color is None:
            color = (
                writer is print
                and sys.stdout.isatty()
                and "NO_COLOR" not in os.environ
            )
        self.color = color

    def line(self, value: str = "") -> None:
        self.writer(value)

    def title(self, title: str, *, icon: str = "◆") -> None:
        self.writer("")
        self.writer(self._style(f"{icon} {title}", "bold"))

    def welcome(
        self,
        *,
        version: str,
        workspace: Path,
        model: str,
        mode: str,
        session: str | None,
    ) -> None:
        self.writer(self._style(f"Mini Pi {version}", "bold"))
        self.writer(f"  工作区  {workspace}")
        self.writer(f"  模型    {model}")
        self.writer(f"  模式    {mode}")
        if session:
            self.writer(f"  会话    {session}")
        self.writer("  帮助    /help")

    def key_values(
        self,
        title: str,
        rows: Iterable[tuple[str, object]],
    ) -> None:
        self.title(title)
        for key, value in rows:
            self.writer(f"  {key:<10} {value}")

    def task_started(self, task: str, workspace: Path) -> None:
        self.title("任务", icon="●")
        self.writer(f"  {task}")
        self.writer(self._dim(f"  工作区  {workspace}"))

    def tool_started(self, name: str) -> None:
        label = TOOL_LABELS.get(name, name)
        self.writer(self._dim(f"  → {label}"))

    def answer(self, answer: str) -> None:
        self.title("回答", icon="●")
        for line in answer.rstrip().splitlines() or [""]:
            self.writer(f"  {line}")

    def verification(
        self,
        *,
        passed: bool,
        elapsed_seconds: float,
        output: str = "",
        show_output: bool = False,
        full_output: bool = False,
    ) -> None:
        symbol = "✓" if passed else "✗"
        state = "通过" if passed else "失败"
        self.title("验收", icon=symbol)
        self.writer(f"  {state}  {elapsed_seconds:.2f}s")
        if show_output and output.strip():
            excerpt = (
                _tail_lines(output, 20)
                if full_output
                else _diagnostic_excerpt(output)
            )
            self.writer(self._indent(excerpt))

    def run_report(
        self,
        report: "AgentRunReport",
        *,
        report_path: Path | None = None,
        include_answer: bool = True,
    ) -> None:
        symbol, label = STATUS_LABELS.get(
            report.status,
            ("!", report.status),
        )
        self.title(label, icon=symbol)
        if include_answer and report.final_answer:
            for line in report.final_answer.rstrip().splitlines():
                self.writer(f"  {line}")
        if report.error:
            self.writer(self._style(f"  {report.error}", "red"))

        self.render_changes(report.change_details)

        verification = report.verification or {}
        if verification:
            passed = bool(verification.get("passed"))
            state = "通过" if passed else "失败"
            symbol = "✓" if passed else "✗"
            elapsed = float(verification.get("elapsed_seconds", 0.0))
            self.writer(f"  {symbol} 验收 {state} · {elapsed:.2f}s")

        self.writer(
            "  "
            f"{report.rounds} 轮 · {report.tool_calls} 次工具调用 · "
            f"{report.elapsed_seconds:.2f}s"
        )
        if report_path is not None:
            self.writer(self._dim(f"  报告  {report_path}"))

    def render_changes(self, details: list[dict[str, Any]]) -> None:
        if not details:
            self.writer("  修改  无文件变更")
            return
        self.writer(f"  修改  {len(details)} 个文件")
        status_marks = {"added": "A", "modified": "M", "deleted": "D"}
        for index, item in enumerate(details, start=1):
            path = str(item.get("path", ""))
            mark = status_marks.get(str(item.get("status")), "M")
            lines = [str(value) for value in item.get("lines", [])]
            location = f"{path}:{lines[0].split('-', 1)[0]}" if lines else path
            counts = _change_counts(item)
            ranges = ", ".join(f"L{value}" for value in lines)
            suffix = " · ".join(value for value in (ranges, counts) if value)
            self.writer(
                f"    {index}. {mark} {location}"
                + (f"  {suffix}" if suffix else "")
            )

    def diff(self, text: str, *, path: str | None = None) -> None:
        self.title(f"差异{f' · {path}' if path else ''}", icon="Δ")
        self.writer(self._indent(text.rstrip() or "没有差异"))

    def _indent(self, text: str) -> str:
        return "\n".join(f"  {line}" for line in text.splitlines())

    def _dim(self, value: str) -> str:
        return self._style(value, "dim")

    def _style(self, value: str, style: str) -> str:
        if not self.color:
            return value
        codes = {"bold": "1", "dim": "2", "red": "31"}
        return f"\033[{codes[style]}m{value}\033[0m"


def _change_counts(item: dict[str, Any]) -> str:
    added = int(item.get("added", 0))
    removed = int(item.get("removed", 0))
    values: list[str] = []
    if added:
        values.append(f"+{added}")
    if removed:
        values.append(f"-{removed}")
    return " ".join(values)


def _tail_lines(value: str, limit: int) -> str:
    lines = value.rstrip().splitlines()
    if len(lines) <= limit:
        return "\n".join(lines)
    return "... 前面的输出已省略 ...\n" + "\n".join(lines[-limit:])


def _diagnostic_excerpt(value: str) -> str:
    markers = (
        "FAIL:",
        "ERROR:",
        "AssertionError",
        "Error:",
        "Exception:",
        "FAILED ",
        "Ran ",
        "File ",
    )
    selected = [
        line.strip()
        for line in value.splitlines()
        if line.strip() and any(marker in line for marker in markers)
    ]
    if not selected:
        return _tail_lines(value, 8)
    return "\n".join(selected[-10:])
