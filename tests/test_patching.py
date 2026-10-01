import tempfile
import unittest
from pathlib import Path

from mini_pi.patching import PatchError, apply_unified_diff
from mini_pi.protection import PathProtector


class PatchingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.protector = PathProtector.from_rules(("tests", ".env", ".git"))

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_applies_unified_diff(self) -> None:
        target = self.root / "calculator.py"
        target.write_text(
            "def add(a, b):\n    return a - b\n",
            encoding="utf-8",
        )

        result = apply_unified_diff(
            root=self.root,
            protector=self.protector,
            diff_text=(
                "--- a/calculator.py\n"
                "+++ b/calculator.py\n"
                "@@ -1,2 +1,2 @@\n"
                " def add(a, b):\n"
                "-    return a - b\n"
                "+    return a + b\n"
            ),
        )

        self.assertEqual(result.changed_files, ("calculator.py",))
        self.assertIn("return a + b", target.read_text(encoding="utf-8"))

    def test_conflict_leaves_every_file_unchanged(self) -> None:
        first = self.root / "first.py"
        second = self.root / "second.py"
        first.write_text("before\n", encoding="utf-8")
        second.write_text("actual\n", encoding="utf-8")

        patch = (
            "--- a/first.py\n"
            "+++ b/first.py\n"
            "@@ -1 +1 @@\n"
            "-before\n"
            "+after\n"
            "--- a/second.py\n"
            "+++ b/second.py\n"
            "@@ -1 +1 @@\n"
            "-missing\n"
            "+changed\n"
        )

        with self.assertRaises(PatchError):
            apply_unified_diff(
                root=self.root,
                protector=self.protector,
                diff_text=patch,
            )

        self.assertEqual(first.read_text(encoding="utf-8"), "before\n")
        self.assertEqual(second.read_text(encoding="utf-8"), "actual\n")

    def test_protected_test_file_is_rejected(self) -> None:
        tests = self.root / "tests"
        tests.mkdir()
        target = tests / "test_app.py"
        target.write_text("VALUE = 1\n", encoding="utf-8")

        with self.assertRaises(PatchError):
            apply_unified_diff(
                root=self.root,
                protector=self.protector,
                diff_text=(
                    "--- a/tests/test_app.py\n"
                    "+++ b/tests/test_app.py\n"
                    "@@ -1 +1 @@\n"
                    "-VALUE = 1\n"
                    "+VALUE = 2\n"
                ),
            )

        self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 1\n")


if __name__ == "__main__":
    unittest.main()
