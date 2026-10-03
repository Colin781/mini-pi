import tempfile
import unittest
from pathlib import Path

from mini_pi.cli import build_parser
from mini_pi.repl import ReplConfig


class InteractiveCliTest(unittest.TestCase):
    def test_no_task_enters_interactive_mode(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--workspace", "example"])

        self.assertIsNone(args.task)
        self.assertEqual(args.workspace, Path("example"))

    def test_task_keeps_one_shot_mode(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["--workspace", "example", "修复测试"]
        )

        self.assertEqual(args.task, "修复测试")

    def test_repl_mode_argument(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--mode", "chat"])
        self.assertEqual(args.mode, "chat")

    def test_resume_and_continue_are_mutually_exclusive(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--resume", "abc", "--continue"])

    def test_conversation_budget_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                ReplConfig(
                    workspace=Path(directory),
                    conversation_chars=999,
                )


if __name__ == "__main__":
    unittest.main()
