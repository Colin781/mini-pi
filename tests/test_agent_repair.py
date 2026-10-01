import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent import (
    build_parser,
    run_agent,
)


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


if __name__ == "__main__":
    unittest.main()
