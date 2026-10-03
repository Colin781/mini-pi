import tempfile
import unittest
from pathlib import Path

from mini_pi.sessions import SessionStore


class SessionStoreTest(unittest.TestCase):
    def test_create_append_resume_and_delete_messages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            store = SessionStore(root / "sessions")
            session = store.create(
                workspace=workspace,
                model="deepseek-flash",
                mode="auto",
                name="订单修复",
            )
            user = store.append_message(
                session.session_id,
                role="user",
                content="修复订单",
                mode="agent",
            )
            store.append_message(
                session.session_id,
                role="assistant",
                content="已经完成",
                mode="agent",
            )

            self.assertEqual(len(store.load_messages(session.session_id)), 2)
            self.assertEqual(
                store.latest_for_workspace(workspace).session_id,
                session.session_id,
            )

            store.remove_messages(session.session_id, [user["message_id"]])
            messages = store.load_messages(session.session_id)
            self.assertEqual([item["role"] for item in messages], ["assistant"])

    def test_truncated_final_jsonl_line_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            store = SessionStore(root / "sessions")
            session = store.create(
                workspace=workspace,
                model="model",
                mode="auto",
            )
            store.append_message(
                session.session_id,
                role="user",
                content="第一条",
                mode="chat",
            )
            path = (
                root
                / "sessions"
                / session.session_id
                / "messages.jsonl"
            )
            with path.open("a", encoding="utf-8") as stream:
                stream.write('{"type":"message"')

            messages = store.load_messages(session.session_id)
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0]["content"], "第一条")

            store.append_message(
                session.session_id,
                role="assistant",
                content="恢复后继续写入",
                mode="chat",
            )
            recovered = store.load_messages(session.session_id)
            self.assertEqual(len(recovered), 2)
            self.assertEqual(recovered[-1]["content"], "恢复后继续写入")

    def test_secrets_are_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            store = SessionStore(root / "sessions")
            session = store.create(
                workspace=workspace,
                model="model",
                mode="chat",
            )
            store.append_message(
                session.session_id,
                role="user",
                content="API_KEY=super-secret-value",
                mode="chat",
            )
            raw = (
                root / "sessions" / session.session_id / "messages.jsonl"
            ).read_text(encoding="utf-8")
            self.assertNotIn("super-secret-value", raw)


if __name__ == "__main__":
    unittest.main()
