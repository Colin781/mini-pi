import tempfile
import unittest
from pathlib import Path

from mini_pi.change_tracking import ChangeTracker


class ChangeTrackerTest(unittest.TestCase):
    def test_patch_records_clickable_line_ranges_and_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "calculator.py"
            target.write_text("one\ntwo\nthree\n", encoding="utf-8")
            tracker = ChangeTracker()

            tracker.record_patch(
                "--- a/calculator.py\n"
                "+++ b/calculator.py\n"
                "@@ -2,1 +2,2 @@\n"
                "-two\n"
                "+two changed\n"
                "+two extra\n"
            )

            details = tracker.summarize(
                ["calculator.py"],
                root=root,
                before_paths={"calculator.py"},
            )

            self.assertEqual(details[0]["lines"], ["2-3"])
            self.assertEqual(details[0]["added"], 2)
            self.assertEqual(details[0]["removed"], 1)
            self.assertEqual(details[0]["status"], "modified")

    def test_replacement_records_its_start_line(self) -> None:
        tracker = ChangeTracker()
        tracker.record_replacement(
            "service.py",
            "first\nold value\nlast\n",
            "old value",
            "new value",
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "service.py").write_text("updated\n", encoding="utf-8")
            details = tracker.summarize(
                ["service.py"],
                root=root,
                before_paths={"service.py"},
            )

        self.assertEqual(details[0]["lines"], ["2"])


if __name__ == "__main__":
    unittest.main()
