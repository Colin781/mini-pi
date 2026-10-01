import unittest
from types import SimpleNamespace

from mini_pi.chat import run_chat


class RecordingCompletions:
    def __init__(self) -> None:
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content="我是简洁回答", tool_calls=[])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class ChatTest(unittest.TestCase):
    def test_chat_does_not_send_tools_or_repository_context(self) -> None:
        completions = RecordingCompletions()
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )

        result = run_chat(
            "你好",
            model="deepseek-flash",
            conversation_history=[
                {"role": "user", "content": "上一句"},
                {"role": "assistant", "content": "上一条回答"},
            ],
            client=client,
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.answer, "我是简洁回答")
        call = completions.calls[0]
        self.assertNotIn("tools", call)
        self.assertNotIn("tool_choice", call)
        self.assertEqual(call["messages"][-1]["content"], "你好")
        self.assertIn("deepseek-flash", call["messages"][0]["content"])


if __name__ == "__main__":
    unittest.main()
