from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mini_pi.tracing import redact


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionError(ValueError):
    """会话不存在或持久化数据损坏。"""


@dataclass(slots=True)
class SessionMetadata:
    schema_version: int
    session_id: str
    name: str
    workspace: str
    model: str
    mode: str
    created_at: str
    updated_at: str
    summary: str = ""
    summarized_message_count: int = 0

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SessionMetadata":
        try:
            return cls(
                schema_version=int(value["schema_version"]),
                session_id=str(value["session_id"]),
                name=str(value["name"]),
                workspace=str(value["workspace"]),
                model=str(value["model"]),
                mode=str(value["mode"]),
                created_at=str(value["created_at"]),
                updated_at=str(value["updated_at"]),
                summary=str(value.get("summary", "")),
                summarized_message_count=int(
                    value.get("summarized_message_count", 0)
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise SessionError(f"会话元数据无效：{error}") from error


class SessionStore:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def _directory(self, session_id: str) -> Path:
        if not session_id or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789-_"
            for character in session_id.casefold()
        ):
            raise SessionError(f"非法会话 ID：{session_id!r}")
        return self.root / session_id

    def _metadata_path(self, session_id: str) -> Path:
        return self._directory(session_id) / "metadata.json"

    def _atomic_json(self, path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(redact(value), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    def _append_jsonl(self, path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._repair_trailing_record(path)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(redact(value), ensure_ascii=False, separators=(",", ":"))
                + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())

    def _repair_trailing_record(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise SessionError(f"无法读取 {path.name}：{error}") from error
        lines = text.splitlines()
        valid_lines: list[str] = []
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError as error:
                if index != len(lines) - 1:
                    raise SessionError(
                        f"{path.name} 第 {index + 1} 行损坏：{error}"
                    ) from error
                break
            valid_lines.append(line)
        repaired = "\n".join(valid_lines)
        if repaired:
            repaired += "\n"
        if repaired != text:
            temporary = path.with_suffix(path.suffix + ".repair.tmp")
            temporary.write_text(repaired, encoding="utf-8")
            temporary.replace(path)

    def create(
        self,
        *,
        workspace: Path,
        model: str,
        mode: str,
        name: str | None = None,
    ) -> SessionMetadata:
        session_id = uuid.uuid4().hex[:12]
        now = utc_now()
        metadata = SessionMetadata(
            schema_version=1,
            session_id=session_id,
            name=(name or f"会话 {session_id[:6]}").strip(),
            workspace=str(workspace.expanduser().resolve()),
            model=model,
            mode=mode,
            created_at=now,
            updated_at=now,
        )
        self._atomic_json(self._metadata_path(session_id), asdict(metadata))
        return metadata

    def load(self, session_id: str) -> SessionMetadata:
        path = self._metadata_path(session_id)
        if not path.is_file():
            raise SessionError(f"会话不存在：{session_id}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SessionError(f"无法读取会话 {session_id}：{error}") from error
        if not isinstance(value, dict):
            raise SessionError(f"会话元数据必须是对象：{session_id}")
        return SessionMetadata.from_dict(value)

    def update(self, metadata: SessionMetadata, **changes: Any) -> SessionMetadata:
        allowed = {
            "name",
            "workspace",
            "model",
            "mode",
            "summary",
            "summarized_message_count",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise SessionError(f"不能更新会话字段：{', '.join(sorted(unknown))}")
        values = asdict(metadata)
        values.update(changes)
        values["updated_at"] = utc_now()
        updated = SessionMetadata.from_dict(values)
        self._atomic_json(self._metadata_path(updated.session_id), asdict(updated))
        return updated

    def append_message(
        self,
        session_id: str,
        *,
        role: str,
        content: str,
        mode: str,
    ) -> dict[str, str]:
        if role not in {"user", "assistant"}:
            raise SessionError(f"不支持的消息角色：{role}")
        message = {
            "type": "message",
            "message_id": uuid.uuid4().hex,
            "timestamp": utc_now(),
            "role": role,
            "content": content,
            "mode": mode,
        }
        self._append_jsonl(
            self._directory(session_id) / "messages.jsonl",
            message,
        )
        metadata = self.load(session_id)
        self.update(metadata)
        return {
            "message_id": message["message_id"],
            "role": role,
            "content": str(redact(content)),
            "mode": mode,
        }

    def remove_messages(self, session_id: str, message_ids: list[str]) -> None:
        if not message_ids:
            return
        self._append_jsonl(
            self._directory(session_id) / "messages.jsonl",
            {
                "type": "messages_deleted",
                "timestamp": utc_now(),
                "message_ids": message_ids,
            },
        )
        self.update(self.load(session_id))

    def _records(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        records: list[dict[str, Any]] = []
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                if index == len(lines) - 1:
                    break
                raise SessionError(
                    f"{path.name} 第 {index + 1} 行损坏：{error}"
                ) from error
            if not isinstance(value, dict):
                raise SessionError(f"{path.name} 第 {index + 1} 行必须是对象")
            records.append(value)
        return records

    def load_messages(self, session_id: str) -> list[dict[str, str]]:
        records = self._records(
            self._directory(session_id) / "messages.jsonl"
        )
        messages: list[dict[str, str]] = []
        deleted: set[str] = set()
        for record in records:
            if record.get("type") == "messages_deleted":
                deleted.update(str(item) for item in record.get("message_ids", []))
            elif record.get("type") == "message":
                messages.append(
                    {
                        "message_id": str(record.get("message_id", "")),
                        "role": str(record.get("role", "")),
                        "content": str(record.get("content", "")),
                        "mode": str(record.get("mode", "agent")),
                    }
                )
        return [
            message
            for message in messages
            if message["message_id"] not in deleted
        ]

    def append_run(self, session_id: str, report: dict[str, Any]) -> None:
        self._append_jsonl(
            self._directory(session_id) / "runs.jsonl",
            {"type": "run", "timestamp": utc_now(), "report": report},
        )
        self.update(self.load(session_id))

    def list(self) -> list[SessionMetadata]:
        if not self.root.exists():
            return []
        sessions: list[SessionMetadata] = []
        for path in self.root.iterdir():
            if not path.is_dir():
                continue
            try:
                sessions.append(self.load(path.name))
            except SessionError:
                continue
        return sorted(sessions, key=lambda item: item.updated_at, reverse=True)

    def latest_for_workspace(self, workspace: Path) -> SessionMetadata | None:
        target = str(workspace.expanduser().resolve())
        return next(
            (item for item in self.list() if item.workspace == target),
            None,
        )

    def delete(self, session_id: str) -> None:
        directory = self._directory(session_id)
        if not directory.exists():
            raise SessionError(f"会话不存在：{session_id}")
        shutil.rmtree(directory)
