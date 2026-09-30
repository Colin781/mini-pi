import json
import tempfile
import unittest
from pathlib import Path

from mini_pi.reporting import AgentRunReport


class AgentRunReportTest(unittest.TestCase):
    def test_write_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = (
                Path(directory)
                / "nested"
                / "report.json"
            )

            report = AgentRunReport(
                schema_version=1,
                status="completed",
                exit_code=0,
                task="修复测试",
                workspace="/tmp/workspace",
                model="deepseek-flash",
                started_at="2026-01-01T00:00:00+00:00",
                elapsed_seconds=2.5,
                rounds=3,
                tool_calls=6,
                final_answer="测试通过",
                error=None,
            )

            report.write_json(destination)

            saved = json.loads(
                destination.read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(
                saved["status"],
                "completed",
            )
            self.assertEqual(saved["rounds"], 3)
            self.assertEqual(
                saved["tool_calls"],
                6,
            )
            self.assertFalse(
                destination.with_suffix(
                    ".json.tmp"
                ).exists()
            )


if __name__ == "__main__":
    unittest.main()