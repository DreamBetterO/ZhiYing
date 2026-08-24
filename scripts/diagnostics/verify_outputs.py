from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from pypdf import PdfReader


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="output")
    parser.add_argument("--docx-validator")
    parser.add_argument("--render-dir")
    args = parser.parse_args()
    root = Path(args.output)
    docx_files = list(root.rglob("*.docx"))
    pdf_files = list(root.rglob("*.pdf"))
    if not docx_files or not pdf_files:
        raise SystemExit("DOCX/PDF output missing")
    for docx in docx_files:
        with zipfile.ZipFile(docx) as archive:
            names = set(archive.namelist())
            required = {"[Content_Types].xml", "word/document.xml", "word/styles.xml"}
            missing = required - names
            if missing:
                raise RuntimeError(f"{docx}: missing {sorted(missing)}")
            images = [name for name in names if name.startswith("word/media/")]
            document_xml = archive.read("word/document.xml").decode("utf-8")
            relationships = (
                archive.read("word/_rels/document.xml.rels").decode("utf-8")
                if "word/_rels/document.xml.rels" in names else ""
            )
            video_links = relationships.count("video-study://play/")
            if "回看原视频" in document_xml and video_links < 1:
                raise RuntimeError(f"{docx}: visible source labels exist but video hyperlink relationships are missing")
        if args.docx_validator:
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            subprocess.run([sys.executable, args.docx_validator, str(docx.resolve())], check=True, env=env)
        print(f"DOCX OK: {docx} ({len(images)} embedded images, {video_links} video links)")
    for pdf in pdf_files:
        reader = PdfReader(pdf)
        text = "".join(page.extract_text() or "" for page in reader.pages)
        if not reader.pages or not text.strip():
            raise RuntimeError(f"{pdf}: empty PDF")
        video_links = 0
        for page in reader.pages:
            for annotation_ref in page.get("/Annots") or []:
                annotation = annotation_ref.get_object()
                uri = str((annotation.get("/A") or {}).get("/URI") or "")
                video_links += int("video-study://play/" in uri)
        if "回看原视频" in text and video_links < 1:
            raise RuntimeError(f"{pdf}: visible source labels exist but PDF link annotations are missing")
        print(f"PDF OK: {pdf} ({len(reader.pages)} pages, {len(text)} text chars, {video_links} video links)")
        if args.render_dir:
            render_dir = Path(args.render_dir)
            render_dir.mkdir(parents=True, exist_ok=True)
            prefix = render_dir / f"{pdf.parent.name}-page"
            tool = Path(shutil.which("pdftoppm") or "pdftoppm")
            if tool.suffix.lower() == ".cmd":
                native = tool.parents[2] / "native" / "poppler" / "Library" / "bin" / "pdftoppm.exe"
                if native.exists():
                    tool = native
            subprocess.run([str(tool), "-png", "-r", "120", str(pdf.resolve()), str(prefix.resolve())], check=True)


if __name__ == "__main__":
    main()
