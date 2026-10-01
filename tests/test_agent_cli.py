import unittest
from pathlib import Path

from agent import build_parser


class AgentCliTest(unittest.TestCase):
    def test_report_argument(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "--workspace",
                "example",
                "--report",
                "result.json",
                "修复测试",
            ]
        )

        self.assertEqual(
            args.workspace,
            Path("example"),
        )

        self.assertEqual(
            args.report,
            Path("result.json"),
        )

        self.assertEqual(
            args.task,
            "修复测试",
        )

    def test_v04_arguments(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "--workspace",
                "example",
                "--verify-command",
                (
                    "python -m unittest "
                    "discover -s tests -v"
                ),
                "--max-repairs",
                "3",
                "--protected-path",
                "tests",
                "--allowed-change",
                "calculator.py",
                "修复测试",
            ]
        )

        self.assertEqual(
            args.verify_command[:3],
            (
                "python",
                "-m",
                "unittest",
            ),
        )

        self.assertEqual(
            args.max_repairs,
            3,
        )

        self.assertEqual(
            args.protected_path,
            ["tests"],
        )

        self.assertEqual(
            args.allowed_change,
            ["calculator.py"],
        )


if __name__ == "__main__":
    unittest.main()
