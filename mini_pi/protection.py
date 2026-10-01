from dataclasses import dataclass

from mini_pi.workspace_state import matches_rule, normalize_rule


DEFAULT_PROTECTED_PATHS = (
    ".git",
    ".env",
)

EVALUATION_PROTECTED_PATHS = (
    "tests",
    "evaluation",
    ".git",
    ".env",
)


class ProtectedPathError(ValueError):
    """目标路径受到保护，不能被写入。"""


@dataclass(frozen=True, slots=True)
class PathProtector:
    rules: tuple[str, ...]

    @classmethod
    def from_rules(
        cls,
        rules: tuple[str, ...] | list[str],
    ) -> "PathProtector":
        normalized: list[str] = []

        for rule in rules:
            value = normalize_rule(rule)
            if value not in normalized:
                normalized.append(value)

        return cls(tuple(normalized))

    def is_protected(self, relative_path: str) -> bool:
        return any(
            matches_rule(relative_path, rule)
            for rule in self.rules
        )

    def ensure_writable(self, relative_path: str) -> None:
        if self.is_protected(relative_path):
            raise ProtectedPathError(
                f"路径受到保护，禁止修改：{relative_path}"
            )
