import json
import tempfile
import unittest
from pathlib import Path

from mini_pi.reporting import (
    AgentRunReport,
)


class AgentRunReportTest(
    unittest.TestCase
):
    def test_write_json(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = (
                Path(directory)
                / "nested"
                / "report.json"
            )

            report = AgentRunReport(
                schema_version=4,
                status="success",
                exit_code=0,
                task="修复测试",
                workspace="/tmp/workspace",
                model="deepseek-flash",
                started_at=(
                    "2026-01-01"
                    "T00:00:00+00:00"
                ),
                elapsed_seconds=2.5,
                rounds=3,
                tool_calls=6,
                repair_attempts=1,
                files_read=6,
                unique_files_read=4,
                context_chars=18240,
                search_calls=3,
                patches_applied=2,
                checkpoints_created=3,
                checkpoints_restored=1,
                trace_path="/tmp/run.jsonl",
                final_answer="测试通过",
                error=None,
                verification={
                    "passed": True,
                    "exit_code": 0,
                },
                changed_files=[
                    "calculator.py"
                ],
            )

            report.write_json(
                destination
            )

            saved = json.loads(
                destination.read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(
                saved["status"],
                "success",
            )

            self.assertEqual(
                saved["rounds"],
                3,
            )

            self.assertEqual(
                saved["tool_calls"],
                6,
            )

            self.assertEqual(
                saved[
                    "repair_attempts"
                ],
                1,
            )

            self.assertEqual(saved["files_read"], 6)
            self.assertEqual(saved["unique_files_read"], 4)
            self.assertEqual(saved["context_chars"], 18240)
            self.assertEqual(saved["search_calls"], 3)
            self.assertEqual(saved["patches_applied"], 2)
            self.assertEqual(saved["checkpoints_restored"], 1)
            self.assertEqual(saved["trace_path"], "/tmp/run.jsonl")

            self.assertTrue(
                saved["verification"][
                    "passed"
                ]
            )

            self.assertFalse(
                destination.with_suffix(
                    ".json.tmp"
                ).exists()
            )


if __name__ == "__main__":
    unittest.main()
