import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class AgentRunReport:
    schema_version: int
    status: str
    exit_code: int
    task: str
    workspace: str
    model: str
    started_at: str
    elapsed_seconds: float
    rounds: int
    tool_calls: int
    repair_attempts: int = 0
    files_read: int = 0
    unique_files_read: int = 0
    context_chars: int = 0
    search_calls: int = 0
    patches_applied: int = 0
    checkpoints_created: int = 0
    checkpoints_restored: int = 0
    trace_path: str | None = None
    final_answer: str | None = None
    error: str | None = None
    verification: dict[str, Any] | None = None
    changed_files: list[str] = field(default_factory=list)
    change_details: list[dict[str, Any]] = field(default_factory=list)
    protected_violations: list[str] = field(default_factory=list)
    disallowed_changes: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write_json(self, path: Path) -> None:
        destination = path.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)

        temporary = destination.with_suffix(
            destination.suffix + ".tmp"
        )

        temporary.write_text(
            json.dumps(
                self.to_dict(),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        temporary.replace(destination)
