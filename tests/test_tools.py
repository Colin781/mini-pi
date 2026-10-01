import tempfile
import unittest
from pathlib import Path

from mini_pi.tools import ToolError, ToolExecutor


class ToolExecutorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

        self.executor = ToolExecutor(
            root=self.root,
            confirm=lambda description: True,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_file(self, path: str, content: str) -> Path:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def test_list_files(self) -> None:
        self.write_file("app.py", "print('hello')\n")
        self.write_file("src/service.py", "VALUE = 1\n")
        self.write_file(".venv/ignored.py", "ignored = True\n")

        result = self.executor.list_files()

        self.assertIn("app.py", result)
        self.assertIn("src/service.py", result)
        self.assertNotIn("ignored.py", result)

    def test_search_text(self) -> None:
        self.write_file(
            "service.py",
            "def calculate_total():\n"
            "    return 42\n",
        )

        result = self.executor.search_text("calculate_total")

        self.assertIn("service.py:1", result)
        self.assertIn("def calculate_total()", result)

    def test_search_text_is_case_insensitive_by_default(self) -> None:
        self.write_file("message.txt", "Hello DeepSeek\n")

        result = self.executor.search_text("deepseek")

        self.assertIn("message.txt:1", result)

    def test_repository_summary_and_symbol_tools(self) -> None:
        self.write_file(
            "service.py",
            "def calculate_total(value):\n"
            "    return value * 2\n",
        )
        self.write_file(
            "app.py",
            "from service import calculate_total\n"
            "calculate_total(3)\n",
        )

        summary = self.executor.repository_summary()
        symbols = self.executor.find_symbol("calculate_total")
        references = self.executor.find_references("calculate_total")

        self.assertIn("Repository summary", summary)
        self.assertIn("service.py:1-2", symbols)
        self.assertIn("app.py:1", references)

    def test_read_file_with_line_range(self) -> None:
        self.write_file(
            "numbers.txt",
            "one\ntwo\nthree\nfour\n",
        )

        result = self.executor.read_file(
            "numbers.txt",
            start_line=2,
            end_line=3,
        )

        self.assertIn("2 | two", result)
        self.assertIn("3 | three", result)
        self.assertNotIn("1 | one", result)

    def test_replace_text(self) -> None:
        path = self.write_file(
            "calculator.py",
            "def add(a, b):\n"
            "    return a - b\n",
        )

        result = self.executor.replace_text(
            path="calculator.py",
            old="return a - b",
            new="return a + b",
        )

        self.assertIn("已修改", result)
        self.assertIn(
            "return a + b",
            path.read_text(encoding="utf-8"),
        )

    def test_apply_patch_tool(self) -> None:
        path = self.write_file(
            "calculator.py",
            "def add(a, b):\n"
            "    return a - b\n",
        )

        result = self.executor.apply_patch(
            "--- a/calculator.py\n"
            "+++ b/calculator.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def add(a, b):\n"
            "-    return a - b\n"
            "+    return a + b\n"
        )

        self.assertIn("补丁已应用", result)
        self.assertIn("return a + b", path.read_text(encoding="utf-8"))
        self.assertEqual(self.executor.stats.patches_applied, 1)

    def test_replace_requires_unique_text(self) -> None:
        self.write_file(
            "duplicate.txt",
            "same\nsame\n",
        )

        with self.assertRaises(ToolError):
            self.executor.replace_text(
                path="duplicate.txt",
                old="same",
                new="changed",
            )

    def test_rejected_replacement_does_not_modify_file(self) -> None:
        path = self.write_file("data.txt", "before\n")

        executor = ToolExecutor(
            root=self.root,
            confirm=lambda description: False,
        )

        result = executor.replace_text(
            path="data.txt",
            old="before",
            new="after",
        )

        self.assertEqual(result, "用户拒绝了文件修改")
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            "before\n",
        )

    def test_path_cannot_escape_workspace(self) -> None:
        with self.assertRaises(ToolError):
            self.executor.resolve_path("../outside.txt")

    def test_absolute_path_is_rejected(self) -> None:
        with self.assertRaises(ToolError):
            self.executor.resolve_path("/etc/passwd")

    def test_unsafe_command_is_rejected(self) -> None:
        with self.assertRaises(ToolError):
            self.executor.run_command(
                ["rm", "-rf", "."]
            )

    def test_python_script_execution_is_rejected(self) -> None:
        self.write_file("dangerous.py", "print('danger')\n")

        with self.assertRaises(ToolError):
            self.executor.run_command(
                ["python", "dangerous.py"]
            )

    def test_unittest_command_runs(
        self,
    ) -> None:
        self.write_file(
            "test_demo.py",
            "import unittest\n\n"
            "class DemoTest(unittest.TestCase):\n"
            "    def test_ok(self):\n"
            "        self.assertTrue(True)\n",
        )

        result = self.executor.run_command(
            [
                "python",
                "-m",
                "unittest",
                "discover",
                "-v",
            ]
        )

        self.assertIn(
            "退出码：0",
            result,
        )

if __name__ == "__main__":
    unittest.main()
