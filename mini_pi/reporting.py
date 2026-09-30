import json
from dataclasses import asdict, dataclass
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
    final_answer: str | None
    error: str | None

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