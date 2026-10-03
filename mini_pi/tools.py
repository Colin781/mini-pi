import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
import subprocess
from pathlib import Path
from typing import Any
from mini_pi.change_tracking import ChangeTracker
from mini_pi.checkpoints import CheckpointError, CheckpointManager
from mini_pi.command_policy import (
    CommandPolicyError,
    SafeCommandRunner,
)
from mini_pi.patching import (
    PatchError,
    apply_unified_diff,
    parse_unified_diff,
)
from mini_pi.protection import (
    DEFAULT_PROTECTED_PATHS,
    PathProtector,
    ProtectedPathError,
)
from mini_pi.repository import RepositoryIndex
from mini_pi.tracing import NullTraceWriter
from mini_pi.verification import (
    display_command,
)


MAX_FILE_SIZE = 512_000
MAX_TOOL_OUTPUT = 12_000
MAX_LISTED_FILES = 500

IGNORED_DIRECTORIES = {
    ".git",
    ".idea",
    ".mypy_cache",
    ".mini-pi",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}


class ToolError(Exception):
    """工具参数不合法或工具无法安全执行。"""


ConfirmCallback = Callable[[str], bool]


@dataclass(slots=True)
class ToolStats:
    patches_applied: int = 0
    checkpoints_created: int = 0
    checkpoints_restored: int = 0


class ToolExecutor:
    def __init__(
        self,
        root: Path,
        confirm: ConfirmCallback,
        confirm_sensitive: ConfirmCallback | None = None,
        protected_paths: tuple[str, ...] = DEFAULT_PROTECTED_PATHS,
        checkpoint_manager: CheckpointManager | None = None,
        trace: Any | None = None,
    ) -> None:
        self.root = root.resolve()
        self.confirm = confirm
        self.repository = RepositoryIndex(self.root)
        self.protector = PathProtector.from_rules(protected_paths)
        self._checkpoint_manager = checkpoint_manager
        self.trace = trace or NullTraceWriter()
        self.command_runner = SafeCommandRunner(
            root=self.root,
            confirm=confirm_sensitive or self.confirm,
        )
        self.stats = ToolStats()
        self.change_tracker = ChangeTracker()

    @property
    def checkpoint_manager(self) -> CheckpointManager:
        if self._checkpoint_manager is None:
            self._checkpoint_manager = CheckpointManager(self.root)
        return self._checkpoint_manager

    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> str:
        handlers = {
            "repository_summary": self.repository_summary,
            "list_files": self.list_files,
            "search_text": self.search_text,
            "find_symbol": self.find_symbol,
            "find_references": self.find_references,
            "read_file": self.read_file,
            "replace_text": self.replace_text,
            "apply_patch": self.apply_patch,
            "create_checkpoint": self.create_checkpoint,
            "restore_checkpoint": self.restore_checkpoint,
            "run_command": self.run_command,
            "git_diff": self.git_diff,
        }

        handler = handlers.get(name)
        if handler is None:
            raise ToolError(f"未知工具：{name}")

        try:
            return handler(**arguments)
        except ToolError:
            raise
        except TypeError as error:
            raise ToolError(f"工具参数不正确：{error}") from error
        except (OSError, UnicodeError) as error:
            raise ToolError(str(error)) from error

    def resolve_path(
        self,
        relative_path: str,
        *,
        must_exist: bool = True,
    ) -> Path:
        if not isinstance(relative_path, str):
            raise ToolError("文件路径必须是字符串")

        if not relative_path.strip():
            raise ToolError("文件路径不能为空")

        supplied_path = Path(relative_path)

        if supplied_path.is_absolute():
            raise ToolError("不允许使用绝对路径")

        resolved = (self.root / supplied_path).resolve()

        try:
            resolved.relative_to(self.root)
        except ValueError as error:
            raise ToolError("路径超出了工作目录") from error

        if must_exist and not resolved.exists():
            raise ToolError(f"路径不存在：{relative_path}")

        return resolved

    def iter_files(self, base: Path) -> Iterator[Path]:
        if base.is_file():
            yield base
            return

        if not base.is_dir():
            raise ToolError(f"不是文件或目录：{base}")

        for current_directory, directories, filenames in os.walk(base):
            directories[:] = sorted(
                directory
                for directory in directories
                if directory not in IGNORED_DIRECTORIES
                and not directory.startswith(".")
            )

            current_path = Path(current_directory)

            for filename in sorted(filenames):
                if filename.startswith("."):
                    continue

                path = current_path / filename

                if path.is_symlink():
                    continue

                try:
                    resolved = path.resolve()
                    resolved.relative_to(self.root)
                except (OSError, ValueError):
                    continue

                if resolved.is_file():
                    yield resolved

    def relative_name(self, path: Path) -> str:
        return str(path.relative_to(self.root))

    def repository_summary(self) -> str:
        self.repository.refresh()
        return self.repository.summary()

    def find_symbol(
        self,
        name: str,
        max_results: int = 50,
    ) -> str:
        if not isinstance(name, str) or not name.strip():
            raise ToolError("符号名称不能为空")
        if not isinstance(max_results, int):
            raise ToolError("max_results 必须是整数")

        maximum = max(1, min(max_results, 200))
        self.repository.refresh()
        matches = self.repository.find_symbols(name, maximum)

        if not matches:
            return f"没有找到符号：{name}"

        return "\n".join(
            f"{item.path}:{item.line}-{item.end_line}: "
            f"{item.kind} {item.qualified_name}"
            for item in matches
        )

    def find_references(
        self,
        name: str,
        max_results: int = 50,
    ) -> str:
        if not isinstance(name, str) or not name.strip():
            raise ToolError("符号名称不能为空")
        if not isinstance(max_results, int):
            raise ToolError("max_results 必须是整数")

        maximum = max(1, min(max_results, 200))
        self.repository.refresh()
        matches = self.repository.find_references(name, maximum)

        if not matches:
            return f"没有找到引用：{name}"

        return "\n".join(
            f"{item.path}:{item.line}: {item.kind}: {item.code}"
            for item in matches
        )

    def list_files(self, path: str = ".") -> str:
        base = self.resolve_path(path)
        files: list[str] = []

        for file_path in self.iter_files(base):
            files.append(self.relative_name(file_path))

            if len(files) >= MAX_LISTED_FILES:
                files.append(
                    f"... 文件数量超过 {MAX_LISTED_FILES}，结果已截断"
                )
                break

        if not files:
            return "没有找到文件"

        return "\n".join(files)

    def search_text(
        self,
        query: str,
        path: str = ".",
        case_sensitive: bool = False,
        max_results: int = 50,
    ) -> str:
        if not isinstance(query, str) or not query:
            raise ToolError("搜索文本不能为空")

        if not isinstance(max_results, int):
            raise ToolError("max_results 必须是整数")

        max_results = max(1, min(max_results, 200))
        base = self.resolve_path(path)

        expected = query if case_sensitive else query.casefold()
        matches: list[str] = []

        for file_path in self.iter_files(base):
            try:
                if file_path.stat().st_size > MAX_FILE_SIZE:
                    continue

                content = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            for line_number, line in enumerate(
                content.splitlines(),
                start=1,
            ):
                inspected = line if case_sensitive else line.casefold()

                if expected not in inspected:
                    continue

                matches.append(
                    f"{self.relative_name(file_path)}:"
                    f"{line_number}: {line.strip()}"
                )

                if len(matches) >= max_results:
                    matches.append(
                        f"... 已达到最大结果数量 {max_results}"
                    )
                    return "\n".join(matches)

        if not matches:
            return f"没有找到文本：{query}"

        return "\n".join(matches)

    def read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int = 200,
    ) -> str:
        file_path = self.resolve_path(path)

        if not file_path.is_file():
            raise ToolError(f"不是文件：{path}")

        if file_path.stat().st_size > MAX_FILE_SIZE:
            raise ToolError(
                f"文件超过允许读取的大小：{MAX_FILE_SIZE} 字节"
            )

        if not isinstance(start_line, int) or not isinstance(end_line, int):
            raise ToolError("行号必须是整数")

        if start_line < 1:
            raise ToolError("start_line 必须从 1 开始")

        if end_line < start_line:
            raise ToolError("end_line 不能小于 start_line")

        end_line = min(end_line, start_line + 399)

        try:
            lines = file_path.read_text(
                encoding="utf-8"
            ).splitlines()
        except UnicodeDecodeError as error:
            raise ToolError("文件不是 UTF-8 文本文件") from error

        if not lines:
            return f"{path} 是空文件"

        if start_line > len(lines):
            raise ToolError(
                f"start_line 超出文件范围，文件只有 {len(lines)} 行"
            )

        selected = lines[start_line - 1 : end_line]

        rendered = [
            f"{line_number:4d} | {line}"
            for line_number, line in enumerate(
                selected,
                start=start_line,
            )
        ]

        header = (
            f"文件：{path}\n"
            f"显示行：{start_line}-{start_line + len(selected) - 1}\n"
        )

        return header + "\n".join(rendered)

    def replace_text(
        self,
        path: str,
        old: str,
        new: str,
    ) -> str:
        file_path = self.resolve_path(path)
        relative = self.relative_name(file_path)

        try:
            self.protector.ensure_writable(relative)
        except ProtectedPathError as error:
            raise ToolError(str(error)) from error

        if not file_path.is_file():
            raise ToolError(f"不是文件：{path}")

        if not isinstance(old, str) or not isinstance(new, str):
            raise ToolError("old 和 new 必须是字符串")

        if not old:
            raise ToolError("old 不能为空")

        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise ToolError("文件不是 UTF-8 文本文件") from error

        occurrences = content.count(old)

        if occurrences == 0:
            raise ToolError("文件中没有找到 old 文本")

        if occurrences > 1:
            raise ToolError(
                f"old 文本出现了 {occurrences} 次，"
                "请提供更完整的上下文以保证精确修改"
            )

        description = (
            f"修改文件 {path}，"
            f"替换 {len(old)} 个字符为 {len(new)} 个字符"
        )

        if not self.confirm(description):
            return "用户拒绝了文件修改"

        updated = content.replace(old, new, 1)
        file_path.write_text(updated, encoding="utf-8")
        self.change_tracker.record_replacement(
            relative,
            content,
            old,
            new,
        )
        self.repository.refresh()

        return (
            f"已修改：{path}\n"
            f"原文本行数：{old.count(chr(10)) + 1}\n"
            f"新文本行数：{new.count(chr(10)) + 1}"
        )

    def apply_patch(self, patch: str) -> str:
        try:
            parsed = parse_unified_diff(patch)
            paths = [item.new_path for item in parsed]

            for path in paths:
                self.protector.ensure_writable(path)
        except (PatchError, ProtectedPathError) as error:
            raise ToolError(str(error)) from error

        description = (
            "应用统一 diff，修改文件："
            + ", ".join(paths)
        )

        if not self.confirm(description):
            return "用户拒绝了补丁修改"

        try:
            result = apply_unified_diff(
                root=self.root,
                diff_text=patch,
                protector=self.protector,
            )
        except PatchError as error:
            raise ToolError(str(error)) from error

        self.repository.refresh()
        self.change_tracker.record_patch(patch)
        self.stats.patches_applied += 1
        self.trace.write(
            "patch_applied",
            files=list(result.changed_files),
            patch_sha256=result.patch_sha256,
            added_lines=result.added_lines,
            removed_lines=result.removed_lines,
        )

        return (
            "补丁已应用\n"
            f"文件：{', '.join(result.changed_files)}\n"
            f"新增行：{result.added_lines}\n"
            f"删除行：{result.removed_lines}\n"
            f"SHA-256：{result.patch_sha256}"
        )

    def create_checkpoint(self, label: str = "manual") -> str:
        checkpoint = self.checkpoint_manager.create(label)
        self.stats.checkpoints_created += 1
        self.trace.write(
            "checkpoint_created",
            checkpoint_id=checkpoint.checkpoint_id,
            label=checkpoint.label,
            file_count=len(checkpoint.files),
        )
        return (
            f"检查点已创建：{checkpoint.checkpoint_id}\n"
            f"标签：{checkpoint.label}\n"
            f"文件数：{len(checkpoint.files)}"
        )

    def restore_checkpoint(
        self,
        checkpoint_id: str | None = None,
    ) -> str:
        try:
            checkpoint = self.checkpoint_manager.restore(checkpoint_id)
        except CheckpointError as error:
            raise ToolError(str(error)) from error

        self.repository.refresh()
        self.change_tracker.clear()
        self.stats.checkpoints_restored += 1
        self.trace.write(
            "checkpoint_restored",
            checkpoint_id=checkpoint.checkpoint_id,
            label=checkpoint.label,
        )
        return (
            f"已恢复检查点：{checkpoint.checkpoint_id}\n"
            f"标签：{checkpoint.label}"
        )

    def run_command(
            self,
            command: list[str],
            timeout_seconds: int = 60,
    ) -> str:
        try:
            result = self.command_runner.run(
                command,
                timeout_seconds=timeout_seconds,
            )
        except CommandPolicyError as error:
            raise ToolError(
                str(error)
            ) from error

        return (
            f"命令：{display_command(result.command)}\n"
            f"策略：{result.decision.value}\n"
            f"退出码：{result.exit_code}\n"
            f"超时：{'是' if result.timed_out else '否'}\n"
            f"{self.tail(result.output)}"
        )

    def normalize_command(
            self,
            command: list[str],
    ) -> list[str]:
        assessment = self.command_runner.policy.assess(command)
        if assessment.decision.value == "deny":
            raise ToolError(assessment.reason)
        return self.command_runner.policy.executable_command(
            assessment.command
        )

    def git_diff(self, path: str | None = None) -> str:
        relative: str | None = None
        if path:
            target = self.resolve_path(path, must_exist=False)
            relative = self.relative_name(target)
        pathspec = ["--", relative] if relative else ["--", "."]

        status = subprocess.run(
            ["git", "status", "--short", *pathspec],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=15,
        )

        if status.returncode != 0:
            return (
                "当前工作目录不在 Git 仓库中，"
                "无法生成 Git diff。\n"
                f"{status.stderr.strip()}"
            )

        diff = subprocess.run(
            ["git", "diff", "--no-ext-diff", *pathspec],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=15,
        )

        sections = [
            "Git 状态：",
            status.stdout.strip() or "没有文件状态变化",
            "",
            "Git diff：",
            diff.stdout.strip() or "没有已跟踪文件的内容差异",
        ]

        return self.tail("\n".join(sections))

    @staticmethod
    def tail(text: str) -> str:
        if not text:
            return "命令没有输出"

        if len(text) <= MAX_TOOL_OUTPUT:
            return text

        return (
            f"... 前面的输出已截断 ...\n"
            f"{text[-MAX_TOOL_OUTPUT:]}"
        )
