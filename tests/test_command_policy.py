import tempfile
import unittest
from pathlib import Path

from mini_pi.command_policy import (
    CommandDecision,
    CommandPolicy,
    CommandPolicyError,
    SafeCommandRunner,
)


class CommandPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_classifies_allowed_confirmed_and_denied_commands(self) -> None:
        policy = CommandPolicy(self.root)

        self.assertEqual(
            policy.assess(["python", "-m", "unittest", "-v"]).decision,
            CommandDecision.ALLOW,
        )
        self.assertEqual(
            policy.assess(["python", "-m", "pip", "install", "demo"]).decision,
            CommandDecision.CONFIRM,
        )
        self.assertEqual(
            policy.assess(["rm", "-rf", "."]).decision,
            CommandDecision.DENY,
        )
        self.assertEqual(
            policy.assess(["python", "/tmp/outside.py"]).decision,
            CommandDecision.DENY,
        )
        self.assertEqual(
            policy.assess(
                [
                    "python",
                    "-m",
                    "pip",
                    "install",
                    "demo",
                    "--target=../outside",
                ]
            ).decision,
            CommandDecision.DENY,
        )

    def test_timeout_terminates_process_group(self) -> None:
        (self.root / "test_slow.py").write_text(
            "import time\n"
            "import unittest\n\n"
            "class SlowTest(unittest.TestCase):\n"
            "    def test_slow(self):\n"
            "        time.sleep(2)\n",
            encoding="utf-8",
        )
        runner = SafeCommandRunner(
            root=self.root,
            confirm=lambda description: True,
        )

        result = runner.run(
            ["python", "-m", "unittest", "test_slow"],
            timeout_seconds=0.1,
        )

        self.assertTrue(result.timed_out)
        self.assertEqual(result.exit_code, 124)
        self.assertIn("进程组已终止", result.output)

    def test_denied_command_is_not_started(self) -> None:
        runner = SafeCommandRunner(
            root=self.root,
            confirm=lambda description: True,
        )

        with self.assertRaises(CommandPolicyError):
            runner.run(["rm", "-rf", "."], timeout_seconds=1)

    def test_sensitive_command_requires_confirmation(self) -> None:
        runner = SafeCommandRunner(
            root=self.root,
            confirm=lambda description: False,
        )

        with self.assertRaises(CommandPolicyError):
            runner.run(
                ["python", "-m", "pip", "install", "demo"],
                timeout_seconds=1,
            )


if __name__ == "__main__":
    unittest.main()
