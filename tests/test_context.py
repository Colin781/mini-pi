import unittest

from mini_pi.context import ContextLimits, ContextManager


def assistant_call(call_id: str, name: str, arguments: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments,
                },
            }
        ],
    }


class ContextManagerTest(unittest.TestCase):
    def test_metrics_and_single_file_limit(self) -> None:
        manager = ContextManager(
            task="修复 src/target.py",
            repository_files=["src/target.py", "src/other.py"],
            limits=ContextLimits(
                max_context_chars=1_000,
                max_file_chars=500,
            ),
        )

        clipped = manager.observe_tool_result(
            name="read_file",
            arguments={"path": "src/target.py"},
            result="x" * 800,
        )
        manager.observe_tool_result(
            name="read_file",
            arguments={"path": "src/target.py"},
            result="short",
        )
        manager.observe_tool_result(
            name="find_symbol",
            arguments={"name": "target"},
            result="src/target.py:1-2: function target",
        )

        self.assertLessEqual(len(clipped), 500)
        self.assertEqual(manager.metrics.files_read, 2)
        self.assertEqual(len(manager.metrics.unique_files), 1)
        self.assertEqual(manager.metrics.search_calls, 1)

    def test_named_file_wins_when_budget_is_full(self) -> None:
        manager = ContextManager(
            task="修复 src/target.py",
            repository_files=["src/target.py", "src/other.py"],
            limits=ContextLimits(
                max_context_chars=1_000,
                max_file_chars=500,
            ),
        )
        manager.register_summary("summary" * 40)

        messages = [
            {"role": "user", "content": "task"},
            assistant_call("low", "read_file", '{"path":"src/other.py"}'),
            {"role": "tool", "tool_call_id": "low", "content": "L" * 500},
            assistant_call("high", "read_file", '{"path":"src/target.py"}'),
            {"role": "tool", "tool_call_id": "high", "content": "H" * 500},
        ]

        prepared = manager.prepare_messages(messages)
        tool_messages = [item for item in prepared if item["role"] == "tool"]

        self.assertIn("省略", tool_messages[0]["content"])
        self.assertEqual(tool_messages[1]["content"], "H" * 500)
        self.assertLessEqual(manager.metrics.context_chars, 1_000)


if __name__ == "__main__":
    unittest.main()
