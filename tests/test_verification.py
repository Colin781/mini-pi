import tempfile
import unittest
from pathlib import Path

from mini_pi.verification import (
    VerificationError,
    normalize_verification_command,
    run_verification,
)


class VerificationTest(unittest.TestCase):
    def test_unittest_passes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            (
                root / "test_demo.py"
            ).write_text(
                "import unittest\n\n"
                "class DemoTest(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        self.assertEqual(1 + 1, 2)\n",
                encoding="utf-8",
            )

            result = run_verification(
                root=root,
                command=[
                    "python",
                    "-m",
                    "unittest",
                    "discover",
                    "-v",
                ],
                timeout_seconds=10,
            )

            self.assertTrue(
                result.passed
            )

            self.assertEqual(
                result.exit_code,
                0,
            )

            self.assertFalse(
                result.timed_out
            )

    def test_unsafe_command_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(
            VerificationError
        ):
            normalize_verification_command(
                [
                    "rm",
                    "-rf",
                    ".",
                ]
            )


if __name__ == "__main__":
    unittest.main()
