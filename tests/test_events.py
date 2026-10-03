import unittest

from mini_pi.events import EventTraceProxy, ListEventSink


class FakeTrace:
    path = None
    run_id = "run"

    def __init__(self) -> None:
        self.values = []

    def write(self, event_type, **payload):
        value = {"type": event_type, **payload}
        self.values.append(value)
        return value


class EventsTest(unittest.TestCase):
    def test_trace_events_are_forwarded_to_sink(self) -> None:
        trace = FakeTrace()
        sink = ListEventSink()
        proxy = EventTraceProxy(trace, sink)

        proxy.write("tool_started", tool="read_file", round=2)

        self.assertEqual(trace.values[0]["type"], "tool_started")
        self.assertEqual(sink.events[0].type, "tool_started")
        self.assertEqual(sink.events[0].payload["tool"], "read_file")

    def test_forwarded_events_are_redacted(self) -> None:
        sink = ListEventSink()
        proxy = EventTraceProxy(FakeTrace(), sink)
        proxy.write("request", api_key="sk-abcdefghijk")
        self.assertEqual(sink.events[0].payload["api_key"], "***")


if __name__ == "__main__":
    unittest.main()
