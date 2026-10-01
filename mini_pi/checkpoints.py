import hashlib
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


IGNORED_CHECKPOINT_DIRECTORIES = {
    ".git",
    ".idea",
    ".mini-pi",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}

IGNORED_CHECKPOINT_FILES = {
    ".env",
}


@dataclass(frozen=True, slots=True)
class Checkpoint:
    checkpoint_id: str
    label: str
    created_at: str
    directory: Path
    files: dict[str, str]


class CheckpointError(ValueError):
    """检查点不存在或无法恢复。"""


class CheckpointManager:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.storage = Path(
            tempfile.mkdtemp(prefix="mini-pi-checkpoints-")
        )
        self.checkpoints: dict[str, Checkpoint] = {}
        self.order: list[str] = []

    def _iter_files(self) -> Iterator[Path]:
        for current, directories, filenames in os.walk(self.root):
            directories[:] = sorted(
                name
                for name in directories
                if name not in IGNORED_CHECKPOINT_DIRECTORIES
                and not name.startswith(".")
            )

            current_path = Path(current)

            for filename in sorted(filenames):
                if filename in IGNORED_CHECKPOINT_FILES:
                    continue
                if filename.startswith("."):
                    continue

                path = current_path / filename

                if path.is_symlink() or not path.is_file():
                    continue

                yield path

    def _digest(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def create(self, label: str = "manual") -> Checkpoint:
        checkpoint_id = uuid.uuid4().hex
        directory = self.storage / checkpoint_id
        directory.mkdir(parents=True)
        files: dict[str, str] = {}

        for source in self._iter_files():
            relative = source.relative_to(self.root).as_posix()
            destination = directory / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            files[relative] = self._digest(source)

        checkpoint = Checkpoint(
            checkpoint_id=checkpoint_id,
            label=label,
            created_at=datetime.now(timezone.utc).isoformat(),
            directory=directory,
            files=files,
        )
        self.checkpoints[checkpoint_id] = checkpoint
        self.order.append(checkpoint_id)
        return checkpoint

    def latest(self) -> Checkpoint:
        if not self.order:
            raise CheckpointError("当前没有可用检查点")
        return self.checkpoints[self.order[-1]]

    def get(self, checkpoint_id: str | None = None) -> Checkpoint:
        if checkpoint_id is None:
            return self.latest()

        checkpoint = self.checkpoints.get(checkpoint_id)
        if checkpoint is None:
            raise CheckpointError(f"检查点不存在：{checkpoint_id}")
        return checkpoint

    def restore(self, checkpoint_id: str | None = None) -> Checkpoint:
        checkpoint = self.get(checkpoint_id)
        current = {
            path.relative_to(self.root).as_posix(): path
            for path in self._iter_files()
        }

        for relative, path in current.items():
            if relative not in checkpoint.files:
                path.unlink()

        for relative in checkpoint.files:
            source = checkpoint.directory / relative
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

        return checkpoint

    def discard(self, checkpoint_id: str) -> None:
        checkpoint = self.checkpoints.pop(checkpoint_id, None)
        if checkpoint is None:
            return

        self.order = [
            item
            for item in self.order
            if item != checkpoint_id
        ]
        shutil.rmtree(checkpoint.directory, ignore_errors=True)

    def cleanup(self) -> None:
        self.checkpoints.clear()
        self.order.clear()
        shutil.rmtree(self.storage, ignore_errors=True)
