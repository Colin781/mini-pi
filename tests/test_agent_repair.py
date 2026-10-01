import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent import (
    build_parser,
    run_agent,
)
from mini_pi.tracing import load_trace


def assistant_message(
    content=None,
    tool_calls=None,
):
    return SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
    )


def tool_call(
    call_id: str,
    name: str,
    arguments: str,
):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name=name,
            arguments=arguments,
        ),
    )


class FakeCompletions:
    def __init__(
        self,
        messages,
    ):
        self.messages = iter(messages)

    def create(
        self,
        **kwargs,
    ):
        message = next(
            self.messages
        )

        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=message
                )
            ]
        )


class FakeClient:
    def __init__(
        self,
        messages,
    ):
        self.chat = SimpleNamespace(
            completions=FakeCompletions(
                messages
            )
        )


class AgentRepairTest(
    unittest.TestCase
):
    def test_failed_verification_triggers_repair(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            (
                root / "calculator.py"
            ).write_text(
                "def add(a, b):\n"
                "    return a - b\n",
                encoding="utf-8",
            )

            tests = root / "tests"
            tests.mkdir()

            (
                tests
                / "test_calculator.py"
            ).write_text(
                "import unittest\n"
                "from calculator import add\n\n"
                "class AddTest(unittest.TestCase):\n"
                "    def test_add(self):\n"
                "        self.assertEqual(add(2, 3), 5)\n",
                encoding="utf-8",
            )

            parser = build_parser()

            trace_path = (
                root / ".mini-pi" / "trace.jsonl"
            )

            args = parser.parse_args(
                [
                    "--workspace",
                    str(root),
                    "--yes",
                    "--verify-command",
                    (
                        "python -m unittest "
                        "discover -s tests -v"
                    ),
                    "--protected-path",
                    "tests",
                    "--allowed-change",
                    "calculator.py",
                    "--trace",
                    str(trace_path),
                    "修复 add",
                ]
            )

            client = FakeClient(
                [
                    assistant_message(
                        content="已经完成"
                    ),
                    assistant_message(
                        tool_calls=[
                            tool_call(
                                "call_1",
                                "replace_text",
                                (
                                    '{"path":"calculator.py",'
                                    '"old":"return a - b",'
                                    '"new":"return a + b"}'
                                ),
                            )
                        ]
                    ),
                    assistant_message(
                        content=(
                            "修复完成，测试通过"
                        )
                    ),
                ]
            )

            report = run_agent(
                args,
                client=client,
            )

            self.assertEqual(
                report.status,
                "success",
            )

            self.assertEqual(
                report.repair_attempts,
                1,
            )

            self.assertEqual(
                report.tool_calls,
                1,
            )

            self.assertTrue(
                report.verification[
                    "passed"
                ]
            )

            self.assertEqual(
                report.changed_files,
                ["calculator.py"],
            )

            event_types = [
                event["type"]
                for event in load_trace(trace_path)
            ]

            self.assertEqual(event_types[0], "run_started")
            self.assertIn("checkpoint_created", event_types)
            self.assertIn("model_started", event_types)
            self.assertIn("model_finished", event_types)
            self.assertIn("tool_started", event_types)
            self.assertIn("tool_finished", event_types)
            self.assertIn("verification_failed", event_types)
            self.assertIn("repair_started", event_types)
            self.assertEqual(event_types[-1], "run_finished")

    def test_failed_attempt_restores_checkpoint(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calculator = root / "calculator.py"
            calculator.write_text(
                "def add(a, b):\n"
                "    return a - b\n",
                encoding="utf-8",
            )
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_calculator.py").write_text(
                "import unittest\n"
                "from calculator import add\n\n"
                "class AddTest(unittest.TestCase):\n"
                "    def test_add(self):\n"
                "        self.assertEqual(add(2, 3), 5)\n",
                encoding="utf-8",
            )

            args = build_parser().parse_args(
                [
                    "--workspace",
                    str(root),
                    "--yes",
                    "--verify-command",
                    "python -m unittest discover -s tests -v",
                    "--max-repairs",
                    "0",
                    "--protected-path",
                    "tests",
                    "--allowed-change",
                    "calculator.py",
                    "尝试修复 add",
                ]
            )
            client = FakeClient(
                [
                    assistant_message(
                        tool_calls=[
                            tool_call(
                                "call_1",
                                "replace_text",
                                (
                                    '{"path":"calculator.py",'
                                    '"old":"return a - b",'
                                    '"new":"return a * b"}'
                                ),
                            )
                        ]
                    ),
                    assistant_message(content="已经完成"),
                ]
            )

            report = run_agent(args, client=client)

            self.assertEqual(report.status, "verification_failed")
            self.assertEqual(report.checkpoints_restored, 1)
            self.assertIn("return a - b", calculator.read_text(encoding="utf-8"))
            self.assertEqual(report.changed_files, [])


if __name__ == "__main__":
    unittest.main()
