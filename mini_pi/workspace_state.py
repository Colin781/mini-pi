import hashlib
import os
from pathlib import Path, PurePosixPath


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


def normalize_rule(rule: str) -> str:
    if (
        not isinstance(rule, str)
        or not rule.strip()
    ):
        raise ValueError(
            "文件规则必须是非空字符串"
        )

    path = PurePosixPath(
        rule.replace("\\", "/")
    )

    if (
        path.is_absolute()
        or ".." in path.parts
    ):
        raise ValueError(
            f"非法文件规则：{rule!r}"
        )

    normalized = (
        path.as_posix()
        .strip("/")
    )

    if normalized in {"", "."}:
        raise ValueError(
            "文件规则不能指向整个工作区"
        )

    return normalized


def matches_rule(
    relative_path: str,
    rule: str,
) -> bool:
    normalized_rule = normalize_rule(rule)

    normalized_path = (
        PurePosixPath(relative_path)
        .as_posix()
        .strip("/")
    )

    return (
        normalized_path == normalized_rule
        or normalized_path.startswith(
            normalized_rule + "/"
        )
    )


def snapshot_workspace(
    root: Path,
) -> dict[str, str]:
    resolved_root = root.resolve()
    snapshot: dict[str, str] = {}

    for (
        current_directory,
        directories,
        filenames,
    ) in os.walk(resolved_root):
        directories[:] = sorted(
            directory
            for directory in directories
            if directory
            not in IGNORED_DIRECTORIES
        )

        current_path = Path(
            current_directory
        )

        for filename in sorted(filenames):
            path = current_path / filename

            if (
                path.is_symlink()
                or not path.is_file()
            ):
                continue

            relative = (
                path.relative_to(
                    resolved_root
                )
                .as_posix()
            )

            digest = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()

            snapshot[relative] = digest

    return snapshot


def changed_files(
    before: dict[str, str],
    after: dict[str, str],
) -> list[str]:
    paths = set(before) | set(after)

    return sorted(
        path
        for path in paths
        if before.get(path)
        != after.get(path)
    )


def matching_changes(
    changes: list[str],
    rules: tuple[str, ...],
) -> list[str]:
    return sorted(
        path
        for path in changes
        if any(
            matches_rule(path, rule)
            for rule in rules
        )
    )


def disallowed_changes(
    changes: list[str],
    allowed_rules: tuple[str, ...],
) -> list[str]:
    if not allowed_rules:
        return []

    return sorted(
        path
        for path in changes
        if not any(
            matches_rule(path, rule)
            for rule in allowed_rules
        )
    )
