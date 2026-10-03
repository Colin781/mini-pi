import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from agent import build_parser, run_agent
from mini_pi.events import ListEventSink


class RecordingCompletions:
    def __init__(self) -> None:
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content="完成", tool_calls=[])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)]
        )


class AgentConversationTest(unittest.TestCase):
    def test_previous_turns_are_sent_before_current_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text("value = 1\n", encoding="utf-8")
            completions = RecordingCompletions()
            client = SimpleNamespace(
                chat=SimpleNamespace(completions=completions)
            )
            args = build_parser().parse_args(
                ["--workspace", str(root), "继续完成"]
            )

            report = run_agent(
                args,
                client=client,
                conversation_history=[
                    {"role": "user", "content": "先修改 sample.py"},
                    {"role": "assistant", "content": "第一步已完成"},
                ],
            )

            self.assertEqual(report.status, "completed")
            messages = completions.calls[0]["messages"]
            self.assertEqual(messages[1]["content"], "先修改 sample.py")
            self.assertEqual(messages[2]["content"], "第一步已完成")
            self.assertIn("继续完成", messages[3]["content"])

    def test_repl_can_hide_automatic_git_diff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text("value = 1\n", encoding="utf-8")
            completions = RecordingCompletions()
            client = SimpleNamespace(
                chat=SimpleNamespace(completions=completions)
            )
            args = build_parser().parse_args(
                ["--workspace", str(root), "解释代码"]
            )
            args.show_diff = False

            output = StringIO()
            with redirect_stdout(output):
                report = run_agent(args, client=client)

            self.assertEqual(report.status, "completed")
            self.assertNotIn("工作区状态", output.getvalue())
            self.assertNotIn("Git diff", output.getvalue())

    def test_run_events_and_quiet_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text("value = 1\n", encoding="utf-8")
            completions = RecordingCompletions()
            client = SimpleNamespace(
                chat=SimpleNamespace(completions=completions)
            )
            args = build_parser().parse_args(
                ["--workspace", str(root), "解释代码"]
            )
            args.verbosity = "quiet"
            args.event_sink = ListEventSink()

            output = StringIO()
            with redirect_stdout(output):
                report = run_agent(args, client=client)

            self.assertEqual(report.status, "completed")
            self.assertNotIn("工作目录：", output.getvalue())
            self.assertIn("完成", output.getvalue())
            event_types = [event.type for event in args.event_sink.events]
            self.assertEqual(event_types[0], "run_started")
            self.assertEqual(event_types[-1], "run_finished")


if __name__ == "__main__":
    unittest.main()
