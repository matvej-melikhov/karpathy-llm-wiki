#!/usr/bin/env python3
"""Step 1 of the transcribe pipeline: mechanical raw-text extraction.

Reads a binary source file (PDF or DOCX) and outputs raw markdown to
stdout. Does NOT apply any LLM-based restoration — that is Step 2 and
is performed by the `transcribe` skill agent.

Images found in the source are written to _attachments/ with a unique
name prefix derived from the source filename. The raw markdown output
contains relative image paths (`_attachments/filename-p1-img0.png`)
which the agent converts to Obsidian embeds (![[image.png]]) in Step 2.

Usage:
  python3 bin/transcribe.py <path>          # auto-detect format
  python3 bin/transcribe.py --pages <path>  # only print page count (PDF)

Output:
  stdout: raw markdown text with YAML front-comment block (for agent)
  stderr: progress / warnings
  exit 0 on success, exit 1 on error

Supported formats:
  .pdf   — pymupdf4llm
  .docx  — pandoc

The caller (agent or script) is responsible for:
  - Moving the source to raw/formats/ if needed
  - Running Step 2 (agent restoration)
  - Saving the final .md to raw/
  - Updating raw/meta/ingested.json
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


IMAGE_DIR = Path("_attachments")
SUPPORTED_EXTENSIONS = {".pdf", ".docx"}
LARGE_PDF_PAGE_THRESHOLD = 100   # skip Step 2 restoration above this


def _safe_stem(path: Path) -> str:
    """Filename stem with spaces replaced by hyphens — used as image prefix."""
    return re.sub(r"[^a-zA-Z0-9_\-]", "-", path.stem).strip("-")


# ────────────────────────────────────────────────────────────────────────
# PDF
# ────────────────────────────────────────────────────────────────────────


def pdf_page_count(path: Path) -> int:
    """Return number of pages in a PDF without full extraction."""
    try:
        import pymupdf  # type: ignore
        doc = pymupdf.open(str(path))
        n = len(doc)
        doc.close()
        return n
    except Exception as e:
        print(f"warning: could not count PDF pages: {e}", file=sys.stderr)
        return 0


def extract_pdf(path: Path) -> str:
    """Extract PDF → raw markdown via pymupdf4llm.

    Images are saved to _attachments/<stem>-p<N>-img<M>.png.
    The returned markdown references them as relative paths
    (e.g., `_attachments/paper-p1-img0.png`).
    """
    try:
        import pymupdf4llm  # type: ignore
    except ImportError:
        print("pymupdf4llm not installed. Run: pip3 install --user pymupdf4llm", file=sys.stderr)
        sys.exit(1)

    IMAGE_DIR.mkdir(exist_ok=True)

    stem = _safe_stem(path)
    # pymupdf4llm names images as: <image_path>/<doc_stem>-p<page>-<idx>.png
    # We pass image_path="_attachments" so they land there directly.
    try:
        raw_md = pymupdf4llm.to_markdown(
            str(path),
            write_images=True,
            image_path=str(IMAGE_DIR),
            image_format="png",
        )
    except Exception as e:
        print(f"error extracting PDF: {e}", file=sys.stderr)
        sys.exit(1)

    # pymupdf4llm uses the document filename as image prefix, e.g.:
    #   _attachments/paper-p1-0.png
    # Return as-is; agent will convert to ![[image.png]] in Step 2.
    return raw_md


# ────────────────────────────────────────────────────────────────────────
# DOCX
# ────────────────────────────────────────────────────────────────────────


def extract_docx(path: Path) -> str:
    """Extract DOCX → raw markdown via pandoc.

    Images are extracted to _attachments/. Pandoc names them
    _attachments/<hash>.<ext>. The returned markdown references
    them as relative paths.
    """
    if not shutil.which("pandoc"):
        print("pandoc not installed. Run: brew install pandoc (macOS) or apt install pandoc", file=sys.stderr)
        sys.exit(1)

    IMAGE_DIR.mkdir(exist_ok=True)

    result = subprocess.run(
        [
            "pandoc",
            "--from=docx",
            "--to=gfm",
            "--wrap=none",
            f"--extract-media={IMAGE_DIR}",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"pandoc error: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    return result.stdout


# ────────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", type=Path, help="source file to extract (PDF or DOCX)")
    ap.add_argument("--pages", action="store_true", help="only print page count (PDF only), then exit")
    args = ap.parse_args()

    path: Path = args.source
    if not path.is_file():
        print(f"error: not a file: {path}", file=sys.stderr)
        return 1

    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        print(f"error: unsupported extension '{ext}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}", file=sys.stderr)
        return 1

    # --pages mode (PDF only)
    if args.pages:
        if ext != ".pdf":
            print("--pages is only supported for PDF files", file=sys.stderr)
            return 1
        count = pdf_page_count(path)
        print(count)
        return 0

    # Extract
    print(f"extracting: {path} ({ext[1:].upper()})", file=sys.stderr)

    if ext == ".pdf":
        n_pages = pdf_page_count(path)
        if n_pages:
            print(f"  pages: {n_pages}", file=sys.stderr)
            if n_pages > LARGE_PDF_PAGE_THRESHOLD:
                print(f"  note: large document (>{LARGE_PDF_PAGE_THRESHOLD} pages) — agent will skip restoration", file=sys.stderr)
        raw_md = extract_pdf(path)
    elif ext == ".docx":
        n_pages = 0
        raw_md = extract_docx(path)
    else:
        print(f"error: unsupported extension: {ext}", file=sys.stderr)
        return 1

    # Print metadata as a comment block (agent reads this)
    meta_comment = (
        f"<!-- transcribe-meta\n"
        f"source: {path}\n"
        f"format: {ext[1:]}\n"
        f"pages: {n_pages}\n"
        f"large_doc: {str(n_pages > LARGE_PDF_PAGE_THRESHOLD).lower()}\n"
        f"-->\n\n"
    )
    sys.stdout.write(meta_comment)
    sys.stdout.write(raw_md)
    print(f"done: {len(raw_md)} chars, {raw_md.count(chr(10))} lines", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
