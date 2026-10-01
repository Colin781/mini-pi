import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence


PATH_PATTERN = re.compile(
    r"(?P<path>(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.py)"
)


@dataclass(frozen=True, slots=True)
class ContextLimits:
    max_context_chars: int = 30_000
    max_file_chars: int = 10_000

    def __post_init__(self) -> None:
        if self.max_context_chars < 1_000:
            raise ValueError("max_context_chars 不能小于 1000")
        if self.max_file_chars < 500:
            raise ValueError("max_file_chars 不能小于 500")
        if self.max_file_chars > self.max_context_chars:
            raise ValueError("max_file_chars 不能大于 max_context_chars")


@dataclass(slots=True)
class ContextMetrics:
    files_read: int = 0
    unique_files: set[str] = field(default_factory=set)
    context_chars: int = 0
    search_calls: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "files_read": self.files_read,
            "unique_files_read": len(self.unique_files),
            "context_chars": self.context_chars,
            "search_calls": self.search_calls,
        }


class ContextManager:
    def __init__(
        self,
        *,
        task: str,
        repository_files: Sequence[str],
        limits: ContextLimits,
    ) -> None:
        self.limits = limits
        self.metrics = ContextMetrics()
        self.summary_chars = 0
        self.failed_paths: set[str] = set()
        self.search_hit_paths: set[str] = set()
        self.modified_paths: set[str] = set()
        self.user_named_paths = {
            path
            for path in repository_files
            if path in task or Path(path).name in task
        }

    def register_summary(self, summary: str) -> None:
        self.summary_chars = min(
            len(summary),
            self.limits.max_context_chars,
        )
        self.metrics.context_chars = max(
            self.metrics.context_chars,
            self.summary_chars,
        )

    def _clip(self, content: str, limit: int, label: str) -> str:
        if len(content) <= limit:
            return content

        suffix = f"\n... {label} 已按上下文预算截断"
        return content[: max(0, limit - len(suffix))] + suffix

    def observe_tool_result(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        result: str,
    ) -> str:
        if name == "read_file":
            path = str(arguments.get("path", ""))
            self.metrics.files_read += 1
            if path:
                self.metrics.unique_files.add(path)
            return self._clip(
                result,
                self.limits.max_file_chars,
                "单文件内容",
            )

        if name in {"search_text", "find_symbol", "find_references"}:
            self.metrics.search_calls += 1
            self.search_hit_paths.update(self.extract_paths(result))

        if name == "replace_text":
            path = arguments.get("path")
            if isinstance(path, str) and path:
                self.modified_paths.add(path)

        if name == "apply_patch":
            self.modified_paths.update(self.extract_paths(result))

        return result

    def observe_diagnostics(self, text: str) -> None:
        self.failed_paths.update(self.extract_paths(text))

    def extract_paths(self, text: str) -> set[str]:
        return {
            match.group("path")
            for match in PATH_PATTERN.finditer(text)
        }

    def _priority(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        content: str,
    ) -> int:
        path = arguments.get("path")

        if isinstance(path, str):
            if path in self.user_named_paths:
                return 100
            if path in self.failed_paths:
                return 95
            if path in self.search_hit_paths:
                return 90
            if path in self.modified_paths:
                return 85

        if name == "run_command" and "退出码：0" not in content:
            return 98
        if name in {"find_symbol", "find_references"}:
            return 88
        if name == "search_text":
            return 80
        if name == "read_file":
            return 70
        if name in {
            "replace_text",
            "apply_patch",
            "create_checkpoint",
            "restore_checkpoint",
            "git_diff",
        }:
            return 65
        if name == "repository_summary":
            return 30
        if name == "list_files":
            return 20
        return 50

    def prepare_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        prepared = copy.deepcopy(messages)
        calls: dict[str, tuple[str, dict[str, Any]]] = {}

        for message in prepared:
            if message.get("role") != "assistant":
                continue

            for call in message.get("tool_calls", []):
                function = call.get("function", {})
                raw_arguments = function.get("arguments", "{}")

                try:
                    arguments = json.loads(raw_arguments)
                except (TypeError, json.JSONDecodeError):
                    arguments = {}

                if not isinstance(arguments, dict):
                    arguments = {}

                calls[str(call.get("id"))] = (
                    str(function.get("name", "")),
                    arguments,
                )

        candidates: list[tuple[int, int, dict[str, Any], str]] = []

        for index, message in enumerate(prepared):
            if message.get("role") != "tool":
                continue

            call_id = str(message.get("tool_call_id", ""))
            name, arguments = calls.get(call_id, ("", {}))
            content = str(message.get("content", ""))
            candidates.append(
                (
                    self._priority(
                        name=name,
                        arguments=arguments,
                        content=content,
                    ),
                    index,
                    message,
                    content,
                )
            )

        remaining = max(
            0,
            self.limits.max_context_chars - self.summary_chars,
        )
        selected_chars = 0
        selected_indexes: set[int] = set()

        for _, index, message, content in sorted(
            candidates,
            key=lambda item: (item[0], item[1]),
            reverse=True,
        ):
            if remaining <= 0:
                break

            if len(content) <= remaining:
                selected = content
            elif remaining >= 300:
                selected = self._clip(content, remaining, "工具结果")
            else:
                continue

            message["content"] = selected
            selected_indexes.add(index)
            used = len(selected)
            selected_chars += used
            remaining -= used

        for _, index, message, _ in candidates:
            if index not in selected_indexes:
                message["content"] = (
                    "该工具结果已从当前模型上下文中省略；"
                    "如仍需要，请缩小范围后重新读取。"
                )

        self.metrics.context_chars = max(
            self.metrics.context_chars,
            self.summary_chars + selected_chars,
        )

        return prepared
