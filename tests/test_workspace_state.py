import tempfile
import unittest
from pathlib import Path

from mini_pi.workspace_state import (
    changed_files,
    disallowed_changes,
    matching_changes,
    snapshot_workspace,
)


class WorkspaceStateTest(
    unittest.TestCase
):
    def test_detects_changed_added_and_deleted_files(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            (
                root / "app.py"
            ).write_text(
                "VALUE = 1\n",
                encoding="utf-8",
            )

            (
                root / "old.py"
            ).write_text(
                "OLD = True\n",
                encoding="utf-8",
            )

            before = snapshot_workspace(
                root
            )

            (
                root / "app.py"
            ).write_text(
                "VALUE = 2\n",
                encoding="utf-8",
            )

            (
                root / "new.py"
            ).write_text(
                "NEW = True\n",
                encoding="utf-8",
            )

            (
                root / "old.py"
            ).unlink()

            after = snapshot_workspace(
                root
            )

            self.assertEqual(
                changed_files(
                    before,
                    after,
                ),
                [
                    "app.py",
                    "new.py",
                    "old.py",
                ],
            )

    def test_applies_protected_and_allowed_rules(
        self,
    ) -> None:
        changes = [
            "calculator.py",
            "tests/test_calculator.py",
        ]

        self.assertEqual(
            matching_changes(
                changes,
                ("tests",),
            ),
            [
                "tests/test_calculator.py"
            ],
        )

        self.assertEqual(
            disallowed_changes(
                changes,
                ("calculator.py",),
            ),
            [
                "tests/test_calculator.py"
            ],
        )


if __name__ == "__main__":
    unittest.main()
