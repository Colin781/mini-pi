from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mini_pi.patching import parse_unified_diff


@dataclass(slots=True)
class _TrackedChange:
    lines: set[int] = field(default_factory=set)
    added: int = 0
    removed: int = 0


class ChangeTracker:
    """记录 Agent 编辑过的位置，用于生成简短、可定位的变更摘要。"""

    def __init__(self) -> None:
        self._changes: dict[str, _TrackedChange] = {}

    def record_patch(self, patch_text: str) -> None:
        for file_patch in parse_unified_diff(patch_text):
            change = self._changes.setdefault(
                file_patch.new_path,
                _TrackedChange(),
            )
            for hunk in file_patch.hunks:
                count = max(1, hunk.new_count)
                change.lines.update(
                    range(hunk.new_start, hunk.new_start + count)
                )
                change.added += sum(
                    line.operation == "+" for line in hunk.lines
                )
                change.removed += sum(
                    line.operation == "-" for line in hunk.lines
                )

    def record_replacement(
        self,
        path: str,
        original: str,
        old: str,
        new: str,
    ) -> None:
        offset = original.index(old)
        start = original.count("\n", 0, offset) + 1
        line_count = max(1, len(new.splitlines()))
        change = self._changes.setdefault(path, _TrackedChange())
        change.lines.update(range(start, start + line_count))
        change.added += len(new.splitlines())
        change.removed += len(old.splitlines())

    def clear(self) -> None:
        self._changes.clear()

    def summarize(
        self,
        paths: list[str],
        *,
        root: Path,
        before_paths: set[str],
    ) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for path in paths:
            target = root / path
            if path not in before_paths:
                status = "added"
            elif not target.exists():
                status = "deleted"
            else:
                status = "modified"

            tracked = self._changes.get(path, _TrackedChange())
            lines = _merge_lines(tracked.lines)
            if not lines and target.is_file():
                lines = ["1"]

            result.append(
                {
                    "path": path,
                    "status": status,
                    "lines": lines,
                    "added": tracked.added,
                    "removed": tracked.removed,
                }
            )
        return result


def _merge_lines(lines: set[int]) -> list[str]:
    if not lines:
        return []
    ordered = sorted(lines)
    ranges: list[str] = []
    start = previous = ordered[0]
    for current in ordered[1:]:
        if current == previous + 1:
            previous = current
            continue
        ranges.append(_format_range(start, previous))
        start = previous = current
    ranges.append(_format_range(start, previous))
    return ranges


def _format_range(start: int, end: int) -> str:
    return str(start) if start == end else f"{start}-{end}"
