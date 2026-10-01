import tempfile
import unittest
from pathlib import Path

from mini_pi.repository import RepositoryIndex


class RepositoryIndexTest(unittest.TestCase):
    def test_summary_symbols_and_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "demo"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "service.py").write_text(
                "def calculate_total(value):\n"
                "    return value * 2\n",
                encoding="utf-8",
            )
            (root / "app.py").write_text(
                "from demo.service import calculate_total\n\n"
                "result = calculate_total(4)\n",
                encoding="utf-8",
            )

            index = RepositoryIndex(root)

            symbols = index.find_symbols("calculate_total")
            references = index.find_references("calculate_total")
            summary = index.summary()

            self.assertEqual(symbols[0].path, "demo/service.py")
            self.assertTrue(any(item.kind == "import" for item in references))
            self.assertTrue(any(item.path == "app.py" for item in references))
            self.assertIn("Source packages: demo", summary)
            self.assertIn("3 files", summary)


if __name__ == "__main__":
    unittest.main()
