import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from mini_pi.protection import PathProtector, ProtectedPathError


MAX_PATCH_CHARS = 100_000
MAX_PATCH_FILES = 50

HUNK_HEADER = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$"
)


class PatchError(ValueError):
    """统一 diff 无法安全解析或应用。"""


@dataclass(frozen=True, slots=True)
class PatchLine:
    operation: str
    text: str


@dataclass(frozen=True, slots=True)
class PatchHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple[PatchLine, ...]


@dataclass(frozen=True, slots=True)
class FilePatch:
    old_path: str
    new_path: str
    hunks: tuple[PatchHunk, ...]


@dataclass(frozen=True, slots=True)
class PatchResult:
    changed_files: tuple[str, ...]
    patch_sha256: str
    added_lines: int
    removed_lines: int


def _clean_diff(diff_text: str) -> str:
    stripped = diff_text.strip("\r\n")

    inspected = stripped.strip()

    if inspected.startswith("```diff") and inspected.endswith("```"):
        stripped = inspected
        stripped = stripped[len("```diff") : -3].strip("\n")
    elif inspected.startswith("```") and inspected.endswith("```"):
        stripped = inspected
        stripped = stripped[3:-3].strip("\n")

    return stripped


def _header_path(line: str, marker: str) -> str:
    value = line[len(marker) :].strip().split("\t", 1)[0].strip()
    if not value:
        raise PatchError(f"缺少补丁路径：{line!r}")
    return value


def _relative_patch_path(value: str) -> str:
    if value == "/dev/null":
        return value

    if value.startswith(("a/", "b/")):
        value = value[2:]

    path = PurePosixPath(value.replace("\\", "/"))

    if path.is_absolute() or ".." in path.parts:
        raise PatchError(f"补丁路径不安全：{value!r}")

    normalized = path.as_posix().strip("/")
    if normalized in {"", "."}:
        raise PatchError("补丁路径不能为空")
    return normalized


def parse_unified_diff(diff_text: str) -> tuple[FilePatch, ...]:
    if not isinstance(diff_text, str) or not diff_text.strip():
        raise PatchError("patch 必须是非空字符串")
    if len(diff_text) > MAX_PATCH_CHARS:
        raise PatchError(f"patch 超过 {MAX_PATCH_CHARS} 个字符")

    lines = _clean_diff(diff_text).splitlines()
    patches: list[FilePatch] = []
    index = 0

    while index < len(lines):
        if not lines[index].startswith("--- "):
            index += 1
            continue

        old_path = _relative_patch_path(
            _header_path(lines[index], "--- ")
        )
        index += 1

        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise PatchError("--- 文件头后缺少 +++ 文件头")

        new_path = _relative_patch_path(
            _header_path(lines[index], "+++ ")
        )
        index += 1
        hunks: list[PatchHunk] = []

        while index < len(lines) and not lines[index].startswith("--- "):
            if not lines[index].startswith("@@ "):
                index += 1
                continue

            match = HUNK_HEADER.fullmatch(lines[index])
            if match is None:
                raise PatchError(f"非法 hunk 头：{lines[index]}")

            old_start = int(match.group(1))
            old_count = int(match.group(2) or "1")
            new_start = int(match.group(3))
            new_count = int(match.group(4) or "1")
            index += 1
            hunk_lines: list[PatchLine] = []

            while index < len(lines):
                current = lines[index]
                if current.startswith(("@@ ", "--- ")):
                    break
                if current.startswith("\\ No newline at end of file"):
                    index += 1
                    continue
                if not current or current[0] not in {" ", "+", "-"}:
                    raise PatchError(f"非法补丁行：{current!r}")

                hunk_lines.append(PatchLine(current[0], current[1:]))
                index += 1

            consumed_old = sum(
                item.operation in {" ", "-"}
                for item in hunk_lines
            )
            produced_new = sum(
                item.operation in {" ", "+"}
                for item in hunk_lines
            )

            if consumed_old != old_count or produced_new != new_count:
                raise PatchError(
                    "hunk 行数与头部不一致："
                    f"旧文件 {consumed_old}/{old_count}，"
                    f"新文件 {produced_new}/{new_count}"
                )

            hunks.append(
                PatchHunk(
                    old_start=old_start,
                    old_count=old_count,
                    new_start=new_start,
                    new_count=new_count,
                    lines=tuple(hunk_lines),
                )
            )

        if not hunks:
            raise PatchError(f"文件补丁没有 hunk：{new_path}")
        if new_path == "/dev/null":
            raise PatchError("当前版本暂不允许通过补丁删除文件")
        if old_path != "/dev/null" and old_path != new_path:
            raise PatchError("当前版本暂不支持文件重命名")

        patches.append(FilePatch(old_path, new_path, tuple(hunks)))

        if len(patches) > MAX_PATCH_FILES:
            raise PatchError(f"单次补丁不能超过 {MAX_PATCH_FILES} 个文件")

    if not patches:
        raise PatchError("没有找到 unified diff 文件头")

    names = [item.new_path for item in patches]
    if len(names) != len(set(names)):
        raise PatchError("同一个文件不能在补丁中出现多次")

    return tuple(patches)


def _apply_file_patch(original: str, patch: FilePatch) -> str:
    source = original.splitlines()
    output: list[str] = []
    cursor = 0

    for hunk in patch.hunks:
        target = max(0, hunk.old_start - 1)
        if target < cursor or target > len(source):
            raise PatchError(
                f"hunk 位置超出文件范围：{patch.new_path}:{hunk.old_start}"
            )

        output.extend(source[cursor:target])
        cursor = target

        if len(output) != max(0, hunk.new_start - 1):
            raise PatchError(
                f"hunk 新文件位置冲突：{patch.new_path}:{hunk.new_start}"
            )

        for line in hunk.lines:
            if line.operation in {" ", "-"}:
                if cursor >= len(source) or source[cursor] != line.text:
                    actual = source[cursor] if cursor < len(source) else "<EOF>"
                    raise PatchError(
                        f"补丁上下文冲突：{patch.new_path}:{cursor + 1}，"
                        f"期望 {line.text!r}，实际 {actual!r}"
                    )
                if line.operation == " ":
                    output.append(source[cursor])
                cursor += 1
            else:
                output.append(line.text)

    output.extend(source[cursor:])
    trailing_newline = original.endswith(("\n", "\r")) or patch.old_path == "/dev/null"
    rendered = "\n".join(output)
    if trailing_newline and rendered:
        rendered += "\n"
    return rendered


def _resolve_target(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise PatchError(f"补丁路径超出工作区：{relative}") from error
    if target.is_symlink():
        raise PatchError(f"拒绝修改符号链接：{relative}")
    return target


def apply_unified_diff(
    *,
    root: Path,
    diff_text: str,
    protector: PathProtector,
) -> PatchResult:
    resolved_root = root.resolve()
    patches = parse_unified_diff(diff_text)
    prepared: dict[Path, tuple[str, str, bool]] = {}
    added = 0
    removed = 0

    for patch in patches:
        try:
            protector.ensure_writable(patch.new_path)
        except ProtectedPathError as error:
            raise PatchError(str(error)) from error

        target = _resolve_target(resolved_root, patch.new_path)
        existed = target.exists()

        if patch.old_path == "/dev/null":
            if existed:
                raise PatchError(f"新文件已经存在：{patch.new_path}")
            original = ""
        else:
            if not existed or not target.is_file():
                raise PatchError(f"补丁目标文件不存在：{patch.new_path}")
            try:
                original = target.read_text(encoding="utf-8")
            except UnicodeDecodeError as error:
                raise PatchError(f"目标不是 UTF-8 文本：{patch.new_path}") from error

        updated = _apply_file_patch(original, patch)
        prepared[target] = (original, updated, existed)
        added += sum(
            line.operation == "+"
            for hunk in patch.hunks
            for line in hunk.lines
        )
        removed += sum(
            line.operation == "-"
            for hunk in patch.hunks
            for line in hunk.lines
        )

    temporary_files: dict[Path, Path] = {}
    replaced: list[Path] = []

    try:
        for target, (_, updated, _) in prepared.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".mini-pi.tmp",
                dir=target.parent,
            )
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(updated)
                stream.flush()
                os.fsync(stream.fileno())
            temporary_files[target] = temporary

        for target, temporary in temporary_files.items():
            os.replace(temporary, target)
            replaced.append(target)
    except OSError as error:
        for target in replaced:
            original, _, existed = prepared[target]
            if existed:
                target.write_text(original, encoding="utf-8")
            elif target.exists():
                target.unlink()
        raise PatchError(f"写入补丁失败，已回滚：{error}") from error
    finally:
        for temporary in temporary_files.values():
            if temporary.exists():
                temporary.unlink()

    cleaned = _clean_diff(diff_text)
    return PatchResult(
        changed_files=tuple(patch.new_path for patch in patches),
        patch_sha256=hashlib.sha256(cleaned.encode("utf-8")).hexdigest(),
        added_lines=added,
        removed_lines=removed,
    )
