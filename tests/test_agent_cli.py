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


if __name__ == "__main__":
    unittest.main()