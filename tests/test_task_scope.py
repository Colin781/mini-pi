import tempfile
import unittest
from pathlib import Path

from mini_pi.task_scope import TaskScopeError, plan_task_scope


class TaskScopeTest(unittest.TestCase):
    def test_file_uri_selects_nested_unittest_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "benchmarks" / "task_01_calculator"
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

            plan = plan_task_scope(
                root,
                f"file:{test_file}，这个代码运行会报错，请修改",
            )

            self.assertEqual(plan.workspace, project.resolve())
            self.assertIn("file:tests/test_calculator.py", plan.task)
            self.assertNotIn(str(root), plan.task)
            self.assertEqual(
                plan.verify_command,
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
            self.assertEqual(plan.protected_test_path, "tests")

    def test_source_file_uses_nearest_project_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "packages" / "demo"
            source = project / "src" / "demo" / "service.py"
            source.parent.mkdir(parents=True)
            source.write_text("value = 1\n", encoding="utf-8")
            (project / "pyproject.toml").write_text(
                "[project]\nname='demo'\nversion='0.1.0'\n",
                encoding="utf-8",
            )

            plan = plan_task_scope(root, f"修改 file:{source}")

            self.assertEqual(plan.workspace, project.resolve())
            self.assertIn("file:src/demo/service.py", plan.task)

    def test_outside_file_uri_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            outside = Path(directory) / "outside.py"
            outside.write_text("value = 1\n", encoding="utf-8")

            with self.assertRaises(TaskScopeError):
                plan_task_scope(root, f"读取 file:{outside}")


if __name__ == "__main__":
    unittest.main()
