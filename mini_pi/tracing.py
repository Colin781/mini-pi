import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "password",
    "secret",
    "token",
)

SECRET_PATTERN = re.compile(
    r"(?i)(?:bearer\s+|sk-)[A-Za-z0-9_.-]{8,}"
    r"|(?:api[_-]?key|token|password|secret)=[^\s&]+"
)


def _redact(value: Any, key: str = "") -> Any:
    normalized_key = key.casefold()
    if any(part in normalized_key for part in SENSITIVE_KEY_PARTS):
        return "***"

    if isinstance(value, dict):
        return {
            str(item_key): _redact(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return SECRET_PATTERN.sub("***", value)
    return value


class JsonlTraceWriter:
    def __init__(
        self,
        path: Path,
        *,
        run_id: str | None = None,
    ) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or uuid.uuid4().hex
        self.event_id = 0
        self.lock = threading.Lock()

    def write(self, event_type: str, **payload: Any) -> dict[str, Any]:
        if not event_type:
            raise ValueError("event_type 不能为空")

        with self.lock:
            self.event_id += 1
            event = {
                "schema_version": 1,
                "run_id": self.run_id,
                "event_id": self.event_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": event_type,
                **_redact(payload),
            }

            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        event,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())

            return event


class NullTraceWriter:
    path: Path | None = None
    run_id = ""

    def write(self, event_type: str, **payload: Any) -> dict[str, Any]:
        return {
            "type": event_type,
            **payload,
        }


def load_trace(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"JSONL 第 {line_number} 行无效：{error}"
            ) from error

        if not isinstance(event, dict):
            raise ValueError(f"JSONL 第 {line_number} 行必须是对象")
        events.append(event)

    return events
