import os
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from mini_pi.verification import (
    VerificationError,
    display_command,
    normalize_verification_command,
    run_verification,
)


MAX_FILE_SIZE = 512_000
MAX_TOOL_OUTPUT = 12_000
MAX_LISTED_FILES = 500

IGNORED_DIRECTORIES = {
    ".git",
    ".idea",
    ".mypy_cache",
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


class ToolExecutor:
    def __init__(
        self,
        root: Path,
        confirm: ConfirmCallback,
    ) -> None:
        self.root = root.resolve()
        self.confirm = confirm

    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> str:
        handlers = {
            "list_files": self.list_files,
            "search_text": self.search_text,
            "read_file": self.read_file,
            "replace_text": self.replace_text,
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

        return (
            f"已修改：{path}\n"
            f"原文本行数：{old.count(chr(10)) + 1}\n"
            f"新文本行数：{new.count(chr(10)) + 1}"
        )

    def run_command(
            self,
            command: list[str],
    ) -> str:
        if not isinstance(command, list):
            raise ToolError(
                "command 必须是字符串数组"
            )

        if not command:
            raise ToolError(
                "command 不能为空"
            )

        if not all(
                isinstance(part, str) and part
                for part in command
        ):
            raise ToolError(
                "command 中的每一项"
                "都必须是非空字符串"
            )

        try:
            normalized = (
                self.normalize_command(
                    command
                )
            )
        except VerificationError as error:
            raise ToolError(
                str(error)
            ) from error

        displayed_command = (
            display_command(normalized)
        )

        if not self.confirm(
                f"运行命令：{displayed_command}"
        ):
            return "用户拒绝了命令执行"

        try:
            result = run_verification(
                root=self.root,
                command=command,
                timeout_seconds=60,
            )
        except VerificationError as error:
            raise ToolError(
                str(error)
            ) from error

        return (
            f"命令：{displayed_command}\n"
            f"退出码：{result.exit_code}\n"
            f"{self.tail(result.output)}"
        )

    def normalize_command(
            self,
            command: list[str],
    ) -> list[str]:
        return normalize_verification_command(
            command
        )

    def git_diff(self) -> str:
        status = subprocess.run(
            ["git", "status", "--short", "--", "."],
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
            [
                "git",
                "diff",
                "--no-ext-diff",
                "--",
                ".",
            ],
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
