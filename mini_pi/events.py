from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from mini_pi.tracing import redact


@dataclass(frozen=True, slots=True)
class AgentEvent:
    type: str
    timestamp: str
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, event_type: str, **payload: Any) -> "AgentEvent":
        return cls(
            type=event_type,
            timestamp=datetime.now(timezone.utc).isoformat(),
            payload=payload,
        )


class EventSink(Protocol):
    def emit(self, event: AgentEvent) -> None:
        ...


class NullEventSink:
    def emit(self, event: AgentEvent) -> None:
        return None


class CompositeEventSink:
    def __init__(self, *sinks: EventSink) -> None:
        self.sinks = sinks

    def emit(self, event: AgentEvent) -> None:
        for sink in self.sinks:
            sink.emit(event)


class ListEventSink:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    def emit(self, event: AgentEvent) -> None:
        self.events.append(event)


class EventTraceProxy:
    def __init__(self, trace: Any, sink: EventSink) -> None:
        self.trace = trace
        self.sink = sink

    @property
    def path(self) -> Path | None:
        return self.trace.path

    @property
    def run_id(self) -> str:
        return self.trace.run_id

    def write(self, event_type: str, **payload: Any) -> dict[str, Any]:
        result = self.trace.write(event_type, **payload)
        safe_payload = redact(payload)
        self.sink.emit(AgentEvent.create(event_type, **safe_payload))
        return result
