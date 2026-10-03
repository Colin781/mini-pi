import unittest

from mini_pi.reporting import AgentRunReport
from mini_pi.terminal_ui import TerminalUI


class TerminalUiTest(unittest.TestCase):
    def test_report_shows_file_and_line_location(self) -> None:
        output: list[str] = []
        report = AgentRunReport(
            schema_version=5,
            status="success",
            exit_code=0,
            task="修复计算错误",
            workspace="/tmp/example",
            model="deepseek-flash",
            started_at="2026-01-01T00:00:00+00:00",
            elapsed_seconds=1.25,
            rounds=2,
            tool_calls=3,
            final_answer="已经修复。",
            change_details=[
                {
                    "path": "src/calculator.py",
                    "status": "modified",
                    "lines": ["12-14"],
                    "added": 3,
                    "removed": 1,
                }
            ],
            verification={"passed": True, "elapsed_seconds": 0.2},
        )

        TerminalUI(output.append, color=False).run_report(report)
        rendered = "\n".join(output)

        self.assertIn("✓ 完成", rendered)
        self.assertIn("src/calculator.py:12", rendered)
        self.assertIn("L12-14", rendered)
        self.assertIn("+3 -1", rendered)
        self.assertIn("验收 通过", rendered)


if __name__ == "__main__":
    unittest.main()
