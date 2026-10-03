import tempfile
import unittest
from pathlib import Path

from mini_pi.chat import ChatResult
from mini_pi.repl import InteractiveRepl, ReplConfig, classify_input
from mini_pi.reporting import AgentRunReport
from mini_pi.sessions import SessionStore


def make_report(
    root: Path,
    task: str,
    *,
    status: str = "completed",
) -> AgentRunReport:
    return AgentRunReport(
        schema_version=4,
        status=status,
        exit_code=0,
        task=task,
        workspace=str(root),
        model="deepseek-flash",
        started_at="2026-01-01T00:00:00+00:00",
        elapsed_seconds=0.1,
        rounds=1,
        tool_calls=2,
        final_answer=f"已完成：{task}",
    )


class ReplTest(unittest.TestCase):
    def test_session_is_persisted_and_resumed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SessionStore(root / "sessions")
            first = InteractiveRepl(
                ReplConfig(workspace=root, session_store=store),
                reader=lambda _: "",
                writer=lambda _: None,
            )
            first.handle_line("你好，你是什么模型")
            session_id = first.session.session_id
            first.close()

            second = InteractiveRepl(
                ReplConfig(
                    workspace=root,
                    session_store=store,
                    session_id=session_id,
                ),
                reader=lambda _: "",
                writer=lambda _: None,
            )
            self.assertEqual(len(second.history), 2)
            self.assertEqual(second.history[0]["role"], "user")
            self.assertIn("什么模型", second.history[0]["content"])
            second.close()

    def test_auto_mode_routes_chat_without_running_agent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output: list[str] = []

            def unexpected_agent(*args, **kwargs):
                self.fail("普通对话不应进入 Agent")

            repl = InteractiveRepl(
                ReplConfig(workspace=Path(directory)),
                reader=lambda _: "",
                writer=output.append,
                agent_runner=unexpected_agent,
            )
            repl.handle_line("你好，你是什么模型")

            self.assertEqual(
                output,
                [
                    "我是 Mini Pi 终端助手，当前配置的模型标识是 "
                    "deepseek-flash。"
                ],
            )
            self.assertEqual(repl.turns, [])
            repl.close()

    def test_auto_mode_classification(self) -> None:
        self.assertEqual(classify_input("你好，请介绍一下自己"), "chat")
        self.assertEqual(classify_input("Python 装饰器是什么"), "chat")
        self.assertEqual(classify_input("修复 calculator.py 的错误"), "agent")
        self.assertEqual(classify_input("分析当前项目结构"), "agent")

    def test_file_uri_runs_agent_in_nested_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "benchmarks" / "calculator"
            tests = project / "tests"
            tests.mkdir(parents=True)
            test_file = tests / "test_calculator.py"
            test_file.write_text(
                "import unittest\nfrom calculator import add\n",
                encoding="utf-8",
            )
            (project / "calculator.py").write_text(
                "def add(a, b):\n    return a - b\n",
                encoding="utf-8",
            )
            received = []

            def runner(args, *, conversation_history):
                received.append(args)
                return make_report(project, args.task)

            repl = InteractiveRepl(
                ReplConfig(workspace=root),
                reader=lambda _: "",
                writer=lambda _: None,
                agent_runner=runner,
            )
            repl.handle_line(f"file:{test_file}，运行报错，请修改")

            self.assertEqual(received[0].workspace, project.resolve())
            self.assertIn("file:tests/test_calculator.py", received[0].task)
            self.assertEqual(
                received[0].verify_command,
                (
                    "python",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-v",
                ),
            )
            self.assertIn("tests", received[0].protected_path)
            repl.close()

    def test_forced_chat_uses_lightweight_runner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            received = []
            output: list[str] = []

            def chat_runner(message, **kwargs):
                received.append((message, kwargs))
                return ChatResult(status="completed", answer="简短回答")

            repl = InteractiveRepl(
                ReplConfig(workspace=Path(directory)),
                reader=lambda _: "",
                writer=output.append,
                agent_runner=lambda *args, **kwargs: self.fail(
                    "强制 chat 不应进入 Agent"
                ),
                chat_runner=chat_runner,
            )
            repl.handle_line("/chat 解释一下递归")

            self.assertEqual(received[0][0], "解释一下递归")
            self.assertEqual(output, ["简短回答"])
            repl.close()

    def test_task_reuses_history_and_undo_restores_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "value.txt"
            target.write_text("before", encoding="utf-8")
            received_history: list[list[dict[str, str]]] = []

            def runner(args, *, conversation_history):
                received_history.append(conversation_history)
                target.write_text(args.task, encoding="utf-8")
                return make_report(root, args.task)

            output: list[str] = []
            repl = InteractiveRepl(
                ReplConfig(workspace=root),
                reader=lambda _: "",
                writer=output.append,
                agent_runner=runner,
            )

            repl.handle_line("第一次修改")
            repl.handle_line("继续完善")

            self.assertEqual(received_history[0], [])
            self.assertEqual(
                received_history[1][0],
                {"role": "user", "content": "第一次修改"},
            )
            self.assertEqual(target.read_text(encoding="utf-8"), "继续完善")

            repl.handle_line("/undo")
            self.assertEqual(target.read_text(encoding="utf-8"), "第一次修改")
            self.assertEqual(len(repl.turns), 1)
            repl.close()

    def test_undo_keeps_later_chat_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "value.txt"
            target.write_text("before", encoding="utf-8")

            def runner(args, *, conversation_history):
                target.write_text("after", encoding="utf-8")
                return make_report(root, args.task)

            repl = InteractiveRepl(
                ReplConfig(workspace=root),
                reader=lambda _: "",
                writer=lambda _: None,
                agent_runner=runner,
                chat_runner=lambda *args, **kwargs: ChatResult(
                    status="completed", answer="你好"
                ),
            )
            repl.handle_line("修改 value.txt")
            repl.handle_line("/chat 你好")
            repl.handle_line("/undo")

            self.assertEqual(target.read_text(encoding="utf-8"), "before")
            user_messages = [
                item["content"]
                for item in repl.history
                if item["role"] == "user"
            ]
            self.assertEqual(user_messages, ["你好"])
            repl.close()

    def test_commands_change_session_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output: list[str] = []
            repl = InteractiveRepl(
                ReplConfig(workspace=Path(directory)),
                reader=lambda _: "",
                writer=output.append,
                agent_runner=lambda *args, **kwargs: None,
            )

            repl.handle_line("/model deepseek-chat")
            repl.handle_line("/verify python -m unittest -v")
            repl.handle_line("/status")

            self.assertEqual(repl.config.model, "deepseek-chat")
            self.assertTrue(
                Path(repl.config.verify_command[0]).name.startswith("python")
            )
            self.assertEqual(
                repl.config.verify_command[-3:],
                ("-m", "unittest", "-v"),
            )
            self.assertTrue(any("deepseek-chat" in line for line in output))
            repl.close()

    def test_multiline_input(self) -> None:
        answers = iter(["修复这个问题\\", "并运行测试"])
        with tempfile.TemporaryDirectory() as directory:
            repl = InteractiveRepl(
                ReplConfig(workspace=Path(directory)),
                reader=lambda _: next(answers),
                writer=lambda _: None,
                agent_runner=lambda *args, **kwargs: None,
            )
            self.assertEqual(
                repl._read_task(),
                "修复这个问题\n并运行测试",
            )
            repl.close()

    def test_diff_number_selects_changed_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repl = InteractiveRepl(
                ReplConfig(workspace=root),
                reader=lambda _: "",
                writer=lambda _: None,
            )
            report = make_report(root, "修改两个文件")
            report.changed_files = ["first.py", "second.py"]
            repl.last_report = report

            self.assertEqual(
                repl._resolve_change_argument("2"),
                "second.py",
            )
            with self.assertRaises(ValueError):
                repl._resolve_change_argument("3")
            repl.close()

    def test_failed_task_restores_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "value.txt"
            target.write_text("before", encoding="utf-8")

            def runner(args, *, conversation_history):
                target.write_text("broken", encoding="utf-8")
                report = make_report(root, args.task, status="api_error")
                report.exit_code = 1
                return report

            repl = InteractiveRepl(
                ReplConfig(workspace=root),
                reader=lambda _: "",
                writer=lambda _: None,
                agent_runner=runner,
            )
            repl.handle_line("修改文件")

            self.assertEqual(target.read_text(encoding="utf-8"), "before")
            self.assertEqual(repl.turns, [])
            repl.close()


if __name__ == "__main__":
    unittest.main()
