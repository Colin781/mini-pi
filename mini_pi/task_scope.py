from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote


FILE_URI_PATTERN = re.compile(
    r"file:(?P<path>/[^\s，,；;]+)",
    re.IGNORECASE,
)

PROJECT_MARKERS = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "package.json",
    "Cargo.toml",
    "go.mod",
)


class TaskScopeError(ValueError):
    """文件引用无效或超出 REPL 工作区。"""


@dataclass(frozen=True, slots=True)
class TaskScope:
    workspace: Path
    task: str
    referenced_files: tuple[str, ...] = ()
    verify_command: tuple[str, ...] | None = None
    protected_test_path: str | None = None


def _referenced_paths(
    workspace: Path,
    task: str,
) -> list[tuple[str, Path]]:
    references: list[tuple[str, Path]] = []

    for match in FILE_URI_PATTERN.finditer(task):
        raw = match.group(0)
        raw_path = unquote(match.group("path")).rstrip(
            ")]}'\"。.!?"
        )
        path = Path(raw_path).expanduser().resolve()

        try:
            path.relative_to(workspace)
        except ValueError as error:
            raise TaskScopeError(
                f"文件引用位于工作区外：{path}"
            ) from error

        if not path.exists():
            raise TaskScopeError(f"文件不存在：{path}")
        if not path.is_file():
            raise TaskScopeError(f"引用路径不是文件：{path}")

        references.append((raw, path))

    return references


def _marker_root(path: Path, workspace: Path) -> Path | None:
    current = path.parent
    while True:
        if any((current / marker).is_file() for marker in PROJECT_MARKERS):
            return current
        if current == workspace:
            return None
        if workspace not in current.parents:
            return None
        current = current.parent


def _candidate_root(path: Path, workspace: Path) -> Path:
    relative = path.relative_to(workspace)
    parts = relative.parts

    if "tests" in parts:
        test_index = parts.index("tests")
        if test_index > 0:
            return workspace.joinpath(*parts[:test_index])

    marker_root = _marker_root(path, workspace)
    if marker_root is not None:
        return marker_root

    return path.parent


def _common_scope(candidates: list[Path], workspace: Path) -> Path:
    if not candidates:
        return workspace
    common = Path(os.path.commonpath([str(item) for item in candidates]))
    try:
        common.relative_to(workspace)
    except ValueError:
        return workspace
    return common


def _unittest_configuration(
    referenced_paths: list[Path],
    scope: Path,
) -> tuple[tuple[str, ...] | None, str | None]:
    for path in referenced_paths:
        if not path.name.startswith("test_") or path.suffix != ".py":
            continue
        try:
            content = path.read_text(encoding="utf-8")
            test_directory = path.parent.relative_to(scope).as_posix()
        except (OSError, UnicodeError, ValueError):
            continue
        if "import unittest" not in content:
            continue
        start_directory = test_directory or "."
        return (
            (
                "python",
                "-m",
                "unittest",
                "discover",
                "-s",
                start_directory,
                "-v",
            ),
            test_directory or None,
        )
    return None, None


def plan_task_scope(workspace: Path, task: str) -> TaskScope:
    root = workspace.expanduser().resolve()
    references = _referenced_paths(root, task)
    if not references:
        return TaskScope(workspace=root, task=task)

    paths = [path for _, path in references]
    scope = _common_scope(
        [_candidate_root(path, root) for path in paths],
        root,
    )

    normalized_task = task
    relative_files: list[str] = []
    for raw, path in references:
        relative = path.relative_to(scope).as_posix()
        relative_files.append(relative)
        normalized_task = normalized_task.replace(raw, f"file:{relative}")

    verify_command, protected_test_path = _unittest_configuration(
        paths,
        scope,
    )
    return TaskScope(
        workspace=scope,
        task=normalized_task,
        referenced_files=tuple(relative_files),
        verify_command=verify_command,
        protected_test_path=protected_test_path,
    )
