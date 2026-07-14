from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import render_docx_checked


class RenderDocxCheckedTests(unittest.TestCase):
    def test_macos_seatbelt_refuses_before_subprocess_launch(self) -> None:
        with mock.patch.object(render_docx_checked.sys, "platform", "darwin"):
            with mock.patch.dict(os.environ, {"CODEX_SANDBOX": "seatbelt"}):
                with mock.patch.object(
                    render_docx_checked.subprocess,
                    "run",
                ) as subprocess_run:
                    result = render_docx_checked.run(["input.docx"])

        self.assertEqual(result, render_docx_checked.SANDBOX_EXIT_CODE)
        subprocess_run.assert_not_called()

    def test_finds_highest_installed_documents_renderer_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            older = (
                root
                / "plugins/cache/openai-primary-runtime/documents/26.1.9/skills/"
                "documents/render_docx.py"
            )
            newer = (
                root
                / "plugins/cache/openai-primary-runtime/documents/26.10.1/skills/"
                "documents/render_docx.py"
            )
            older.parent.mkdir(parents=True)
            newer.parent.mkdir(parents=True)
            older.write_text("", encoding="utf-8")
            newer.write_text("", encoding="utf-8")

            result = render_docx_checked.find_documents_renderer(root)

        self.assertEqual(result, newer)

    def test_uses_uv_when_pdf2image_is_not_in_current_interpreter(self) -> None:
        renderer = Path("/tmp/render_docx.py")
        with mock.patch.object(
            render_docx_checked.importlib.util,
            "find_spec",
            return_value=None,
        ):
            with mock.patch.object(
                render_docx_checked.shutil,
                "which",
                return_value="/usr/local/bin/uv",
            ):
                command = render_docx_checked.build_renderer_command(
                    renderer,
                    ["input.docx", "--output_dir", "/tmp/rendered"],
                )

        self.assertEqual(
            command,
            [
                "/usr/local/bin/uv",
                "run",
                "--no-project",
                "--with",
                "pdf2image",
                "python",
                str(renderer),
                "input.docx",
                "--output_dir",
                "/tmp/rendered",
            ],
        )


if __name__ == "__main__":
    unittest.main()
