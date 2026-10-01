import tempfile
import unittest
from pathlib import Path

from mini_pi.tracing import JsonlTraceWriter, load_trace


class JsonlTraceWriterTest(unittest.TestCase):
    def test_writes_replayable_redacted_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.jsonl"
            writer = JsonlTraceWriter(path, run_id="run-1")

            writer.write("run_started", api_key="sk-secret-value")
            writer.write("run_finished", status="success")

            events = load_trace(path)

            self.assertEqual([item["event_id"] for item in events], [1, 2])
            self.assertEqual(events[0]["run_id"], "run-1")
            self.assertEqual(events[0]["api_key"], "***")
            self.assertEqual(events[-1]["type"], "run_finished")


if __name__ == "__main__":
    unittest.main()
