from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.run_codex import build_codex_command, resolve_configured_path


class RunCodexTests(unittest.TestCase):
    def test_dotenv_paths_expand_home_and_environment_variables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            (root / ".env").write_text(
                "MINUTES_HOME=~/custom-minutes\n"
                'RECORDINGS_INBOX="${HOME}/My Inbox"\n',
                encoding="utf-8",
            )
            env = {"HOME": str(home)}

            minutes_home = resolve_configured_path(
                root,
                "MINUTES_HOME",
                environ=env,
            )
            inbox = resolve_configured_path(
                root,
                "RECORDINGS_INBOX",
                environ=env,
            )

        self.assertEqual(minutes_home, (home / "custom-minutes").resolve())
        self.assertEqual(inbox, (home / "My Inbox").resolve())

    def test_process_environment_overrides_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_text(
                "MINUTES_HOME=~/from-dotenv\n",
                encoding="utf-8",
            )

            result = resolve_configured_path(
                root,
                "MINUTES_HOME",
                environ={
                    "HOME": str(root / "home"),
                    "MINUTES_HOME": str(root / "from-environment"),
                },
            )

        self.assertEqual(result, (root / "from-environment").resolve())

    def test_command_adds_each_resolved_writable_path_once(self) -> None:
        root = Path("/tmp/minutes-repo")
        minutes_home = Path("/tmp/minutes-data")
        inbox = Path("/tmp/remind")

        command = build_codex_command(
            "/usr/local/bin/codex",
            root,
            (minutes_home, minutes_home, inbox),
            ("--model", "gpt-test"),
        )

        self.assertEqual(
            command,
            [
                "/usr/local/bin/codex",
                "-C",
                str(root),
                "--add-dir",
                str(minutes_home.resolve()),
                "--add-dir",
                str(inbox.resolve()),
                "--model",
                "gpt-test",
            ],
        )


if __name__ == "__main__":
    unittest.main()
