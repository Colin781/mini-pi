import json
import tempfile
import unittest
from pathlib import Path

from mini_pi.evaluation import (
    EvaluationRecord,
    EvaluationRunner,
    summarize,
)


class EvaluationRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = (
            tempfile.TemporaryDirectory()
        )
        self.root = Path(
            self.temporary_directory.name
        )

        template = (
            self.root
            / "evaluation"
            / "templates"
            / "demo"
        )

        template.mkdir(
            parents=True,
            exist_ok=True,
        )

        (template / "app.py").write_text(
            "VALUE = 1\n",
            encoding="utf-8",
        )

        manifest = {
            "version": 2,
            "cases": [
                {
                    "id": "demo",
                    "title": "Demo task",
                    "task": "Fix the project",
                    "template": (
                        "evaluation/templates/demo"
                    ),
                    "test_command": [
                        "python",
                        "-m",
                        "unittest",
                    ],
                    "timeout_seconds": 30,
                    "command_timeout_seconds": 10,
                    "max_repairs": 2,
                    "protected_paths": ["tests"],
                    "allowed_changed_files": ["app.py"],
                }
            ],
        }

        manifest_path = (
            self.root
            / "evaluation"
            / "cases.json"
        )

        manifest_path.write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )

        (self.root / "agent.py").write_text(
            "print('agent')\n",
            encoding="utf-8",
        )

        self.runner = EvaluationRunner(
            self.root
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_load_cases(self) -> None:
        cases = self.runner.load_cases()

        self.assertEqual(len(cases), 1)
        self.assertEqual(
            cases[0].case_id,
            "demo",
        )
        self.assertEqual(
            cases[0].timeout_seconds,
            30,
        )

    def test_prepare_workspace_copies_template(self) -> None:
        case = self.runner.load_cases()[0]

        workspace = (
            self.runner.prepare_workspace(
                case,
                attempt=1,
                initialize_git=False,
            )
        )

        copied = workspace / "app.py"

        self.assertTrue(copied.is_file())
        self.assertEqual(
            copied.read_text(encoding="utf-8"),
            "VALUE = 1\n",
        )

    def test_prepare_workspace_resets_changes(self) -> None:
        case = self.runner.load_cases()[0]

        workspace = (
            self.runner.prepare_workspace(
                case,
                attempt=1,
                initialize_git=False,
            )
        )

        target = workspace / "app.py"
        target.write_text(
            "VALUE = 999\n",
            encoding="utf-8",
        )

        workspace = (
            self.runner.prepare_workspace(
                case,
                attempt=1,
                initialize_git=False,
            )
        )

        self.assertEqual(
            (workspace / "app.py").read_text(
                encoding="utf-8"
            ),
            "VALUE = 1\n",
        )

    def test_unknown_case_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.runner.select_cases(
                ["missing"]
            )

    def test_summarize(self) -> None:
        records = [
            EvaluationRecord(
                case_id="first",
                title="First",
                attempt=1,
                success=True,
                agent_status="completed",
                agent_exit_code=0,
                verification_exit_code=0,
                elapsed_seconds=10.0,
                rounds=2,
                tool_calls=4,
                repair_attempts=1,
                workspace="workspace/first",
                final_answer="done",
                agent_error=None,
                changed_files=["app.py"],
                protected_violations=[],
                disallowed_changes=[],
                agent_stdout_tail="",
                verification_output="OK",
            ),
            EvaluationRecord(
                case_id="second",
                title="Second",
                attempt=1,
                success=False,
                agent_status="completed",
                agent_exit_code=0,
                verification_exit_code=1,
                elapsed_seconds=20.0,
                rounds=4,
                tool_calls=8,
                repair_attempts=3,
                workspace="workspace/second",
                final_answer="done",
                agent_error=None,
                changed_files=["app.py"],
                protected_violations=[],
                disallowed_changes=[],
                agent_stdout_tail="",
                verification_output="FAILED",
            ),
        ]

        result = summarize(records)

        self.assertEqual(
            result["total_runs"],
            2,
        )

        self.assertEqual(
            result["passed_runs"],
            1,
        )

        self.assertEqual(
            result["success_rate"],
            50.0,
        )

        self.assertEqual(
            result["average_elapsed_seconds"],
            15.0,
        )

        self.assertEqual(
            result["average_rounds"],
            3.0,
        )

        self.assertEqual(
            result["average_tool_calls"],
            6.0,
        )

        self.assertEqual(
            result["average_repair_attempts"],
            2.0,
        )


if __name__ == "__main__":
    unittest.main()
