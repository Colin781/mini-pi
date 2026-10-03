import tempfile
import unittest
from pathlib import Path

from mini_pi.config import ConfigError, load_config


class ConfigTest(unittest.TestCase):
    def test_project_overrides_user_and_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            user = root / "user.toml"
            project = root / "project.toml"
            user.write_text(
                "[model]\nname = 'user-model'\n"
                "[agent]\nmax_steps = 20\n"
                "[repl]\nverbosity = 'quiet'\n",
                encoding="utf-8",
            )
            project.write_text(
                "[model]\nname = 'project-model'\n"
                "[agent]\nmax_steps = 25\n",
                encoding="utf-8",
            )

            config = load_config(
                root,
                user_path=user,
                project_path=project,
                environment={"DEEPSEEK_MODEL": "env-model"},
            )

            self.assertEqual(config.model.name, "project-model")
            self.assertEqual(config.agent.max_steps, 25)
            self.assertEqual(config.repl.verbosity, "quiet")

    def test_invalid_context_limits_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "config.toml"
            project.write_text(
                "[agent]\nmax_context_chars = 1000\nmax_file_chars = 2000\n",
                encoding="utf-8",
            )
            with self.assertRaises(ConfigError):
                load_config(
                    root,
                    user_path=root / "missing.toml",
                    project_path=project,
                    environment={},
                )


if __name__ == "__main__":
    unittest.main()

