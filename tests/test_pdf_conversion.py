from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zhiying.render import convert_docx_to_pdf
from zhiying.utils import TaskCancelled


class PdfConversionTests(unittest.TestCase):
    def test_windows_word_export_is_preferred_when_it_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docx = root / "lesson.docx"
            pdf = root / "lesson.pdf"
            docx.write_bytes(b"docx")

            def fake_run(*_args, **_kwargs) -> None:
                (root / "lesson.word-export.pdf").write_bytes(b"word-pdf")

            with (
                patch("zhiying.render.os.name", "nt"),
                patch("zhiying.render.run_cancellable", side_effect=fake_run),
                patch("zhiying.render.render_pdf_fallback") as fallback,
            ):
                mode = convert_docx_to_pdf(docx, pdf, {})

            self.assertEqual(mode, "local_word")
            self.assertEqual(pdf.read_bytes(), b"word-pdf")
            fallback.assert_not_called()

    def test_windows_word_failure_uses_built_in_pdf_without_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docx = root / "lesson.docx"
            pdf = root / "lesson.pdf"
            docx.write_bytes(b"docx")

            with (
                patch("zhiying.render.os.name", "nt"),
                patch(
                    "zhiying.render.run_cancellable",
                    side_effect=subprocess.CalledProcessError(1, ["powershell.exe"]),
                ),
                patch("zhiying.render.render_pdf_fallback") as fallback,
            ):
                mode = convert_docx_to_pdf(docx, pdf, {})

            self.assertEqual(mode, "built_in")
            fallback.assert_called_once_with({}, pdf, cancel_check=None)

    def test_user_cancellation_is_not_treated_as_conversion_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docx = root / "lesson.docx"
            pdf = root / "lesson.pdf"
            docx.write_bytes(b"docx")

            with (
                patch("zhiying.render.os.name", "nt"),
                patch("zhiying.render.run_cancellable", side_effect=TaskCancelled("cancelled")),
                patch("zhiying.render.render_pdf_fallback") as fallback,
            ):
                with self.assertRaises(TaskCancelled):
                    convert_docx_to_pdf(docx, pdf, {})

            fallback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
