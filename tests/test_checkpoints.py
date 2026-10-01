import tempfile
import unittest
from pathlib import Path

from mini_pi.checkpoints import CheckpointManager


class CheckpointManagerTest(unittest.TestCase):
    def test_restore_recovers_modified_deleted_and_new_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original.py"
            deleted = root / "deleted.py"
            original.write_text("before\n", encoding="utf-8")
            deleted.write_text("keep\n", encoding="utf-8")

            manager = CheckpointManager(root)
            checkpoint = manager.create("before_repair")

            original.write_text("after\n", encoding="utf-8")
            deleted.unlink()
            (root / "new.py").write_text("new\n", encoding="utf-8")

            manager.restore(checkpoint.checkpoint_id)

            self.assertEqual(original.read_text(encoding="utf-8"), "before\n")
            self.assertEqual(deleted.read_text(encoding="utf-8"), "keep\n")
            self.assertFalse((root / "new.py").exists())
            manager.cleanup()


if __name__ == "__main__":
    unittest.main()
