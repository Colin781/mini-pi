import unittest

from mini_pi.conversation import build_conversation_window


class ConversationWindowTest(unittest.TestCase):
    def test_long_history_is_summarized_with_recent_messages_kept(self) -> None:
        history = []
        for index in range(10):
            history.extend(
                [
                    {
                        "role": "user",
                        "content": f"任务 {index} " + "x" * 180,
                    },
                    {
                        "role": "assistant",
                        "content": f"回答 {index} " + "y" * 180,
                    },
                ]
            )

        window = build_conversation_window(
            history,
            max_chars=1_500,
            keep_recent_messages=4,
        )

        self.assertGreater(window.summarized_message_count, 0)
        self.assertIn("任务 0", window.summary)
        self.assertEqual(window.messages[-1]["role"], "assistant")
        self.assertIn("回答 9", window.messages[-1]["content"])
        self.assertLessEqual(window.context_chars, 1_500)

    def test_short_history_is_unchanged(self) -> None:
        history = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好"},
        ]
        window = build_conversation_window(history, max_chars=1_000)
        self.assertEqual(window.messages, history)
        self.assertEqual(window.summary, "")


if __name__ == "__main__":
    unittest.main()

