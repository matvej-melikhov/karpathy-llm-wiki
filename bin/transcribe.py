#!/usr/bin/env python3
"""Transcribe pipeline helper: image extraction for PDF/DOCX sources.

This script handles ONLY the mechanical parts that Claude cannot do natively:
- Extracting embedded images from PDF and DOCX files to _attachments/
- Reporting page count (for large-doc detection)

Text/formula/table content is NOT extracted here — Claude reads the PDF
directly via the Read tool (multimodal), which gives much higher quality
than any text-extraction library, especially for:
  - LaTeX formulas in scanned/typeset PDFs
  - Complex tables
  - Scanned documents with embedded text as images

Usage:
  python3 bin/transcribe.py <path>          # extract images, print manifest
  python3 bin/transcribe.py --pages <path>  # only print page count (PDF)

Output:
  stdout: JSON manifest of extracted images
  stderr: progress / warnings

Supported formats:
  .pdf                       — pymupdf (image extraction by page rendering)
  .docx                      — pandoc  (image extraction via --extract-media)
  .mp3 .wav .m4a .ogg .flac  — whisper-cpp (audio transcription)
  .mp4 .mov .mkv .webm       — ffmpeg + whisper-cpp (video → audio → transcript)

For audio/video sources the script outputs the transcript text directly to
a sidecar file (`_attachments/<stem>.transcript.txt`) and lists it in the
manifest under `transcript`. The agent (Step 2) reads that text and writes
the structured markdown.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


IMAGE_DIR = Path("_attachments")
PDF_EXTENSIONS = {".pdf"}
DOCX_EXTENSIONS = {".docx"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
SUPPORTED_EXTENSIONS = PDF_EXTENSIONS | DOCX_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS
LARGE_PDF_PAGE_THRESHOLD = 100
RENDER_DPI = 200        # DPI for page rendering (higher = better quality)
MERGE_THRESHOLD = 30    # pixels: nearby image regions get merged into one figure
WHISPER_MODEL_ENV = "WHISPER_MODEL"   # path to ggml model; falls back to default
WHISPER_DEFAULT_LANG = "auto"         # whisper-cpp auto-detects language


def _safe_stem(path: Path) -> str:
    """Filename stem safe for use as image prefix (no spaces/special chars)."""
    return re.sub(r"[^a-zA-Z0-9_\-]", "-", path.stem).strip("-")


# ────────────────────────────────────────────────────────────────────────
# PDF image extraction
# ────────────────────────────────────────────────────────────────────────


def pdf_page_count(path: Path) -> int:
    try:
        import pymupdf  # type: ignore
        doc = pymupdf.open(str(path))
        n = len(doc)
        doc.close()
        return n
    except Exception as e:
        print(f"warning: could not count PDF pages: {e}", file=sys.stderr)
        return 0


def _merge_rects(rects: list, threshold: float) -> list:
    """Greedily merge rectangles that are within `threshold` pts of each other.

    Diagrams in PDFs are often composed of many small image objects stored
    separately. Merging their bounding boxes gives us a single coherent figure.
    """
    import pymupdf  # type: ignore

    if not rects:
        return []

    merged = list(rects)
    changed = True
    while changed:
        changed = False
        new_merged: list = []
        used = [False] * len(merged)
        for i, r1 in enumerate(merged):
            if used[i]:
                continue
            combined = r1
            for j, r2 in enumerate(merged):
                if i == j or used[j]:
                    continue
                # Expand r1 by threshold and check intersection with r2
                expanded = pymupdf.Rect(
                    r1.x0 - threshold,
                    r1.y0 - threshold,
                    r1.x1 + threshold,
                    r1.y1 + threshold,
                )
                if expanded.intersects(r2):
                    combined = combined | r2  # union
                    used[j] = True
                    changed = True
            new_merged.append(combined)
            used[i] = True
        merged = new_merged
    return merged


MIN_FIGURE_SIZE = 50    # pts: smaller merged regions are treated as noise


def extract_pdf_images(path: Path) -> list[str]:
    """Extract figures from PDF by rendering.

    Two strategies, chosen per page:

    A) Raster images (scanned PDFs, embedded photos):
       get_image_info() returns large bboxes (>= MIN_FIGURE_SIZE).
       Nearby regions are merged, then each merged region is rendered.

    B) Vector graphics (diagrams drawn with PDF path operators, e.g. Figure 2
       in InstructGPT): get_image_info() returns only tiny icon fragments.
       Fall back to rendering the entire page as one PNG.

    Whole-page rendering is always used when no usable raster regions found.
    Returns list of saved _attachments/ paths.
    """
    try:
        import pymupdf  # type: ignore
    except ImportError:
        print("pymupdf not installed. Run: pip3 install --user pymupdf4llm", file=sys.stderr)
        return []

    IMAGE_DIR.mkdir(exist_ok=True)
    stem = _safe_stem(path)
    saved: list[str] = []

    mat = pymupdf.Matrix(RENDER_DPI / 72, RENDER_DPI / 72)
    doc = pymupdf.open(str(path))

    for page_no, page in enumerate(doc):
        # Pages with no images at all → skip
        if not page.get_images(full=False):
            continue

        # Strategy A: try to find usable raster regions
        raw_bboxes = [
            pymupdf.Rect(info["bbox"])
            for info in page.get_image_info(hashes=False, xrefs=False)
            if pymupdf.Rect(info["bbox"]).width >= MIN_FIGURE_SIZE
               and pymupdf.Rect(info["bbox"]).height >= MIN_FIGURE_SIZE
        ]
        merged = _merge_rects(raw_bboxes, threshold=MERGE_THRESHOLD)
        usable = [r for r in merged if r.width >= MIN_FIGURE_SIZE and r.height >= MIN_FIGURE_SIZE]

        if usable:
            # Render each merged figure region
            for img_idx, rect in enumerate(usable):
                try:
                    pix = page.get_pixmap(matrix=mat, clip=rect)
                    filename = f"{stem}-p{page_no}-img{img_idx}.png"
                    out_path = IMAGE_DIR / filename
                    pix.save(str(out_path))
                    saved.append(str(out_path))
                    print(f"  [raster] saved: {out_path} ({pix.width}x{pix.height}px)", file=sys.stderr)
                except Exception as e:
                    print(f"  warning: skipping p{page_no}:{img_idx} — {e}", file=sys.stderr)
        else:
            # Vector graphics: cannot extract as standalone image file.
            # Agent will write a text description based on Read-tool content.
            print(
                f"  [vector] page {page_no}: vector diagram(s) — skipped, agent will describe",
                file=sys.stderr,
            )

    doc.close()
    return saved


# ────────────────────────────────────────────────────────────────────────
# DOCX image extraction
# ────────────────────────────────────────────────────────────────────────


def extract_docx_images(path: Path) -> list[str]:
    """Extract images from DOCX via pandoc --extract-media.

    pandoc writes images to _attachments/ and returns markdown with
    relative paths. We only need the image files — text is read by
    Claude via native DOCX rendering or pandoc markdown output.
    """
    if not shutil.which("pandoc"):
        print("pandoc not installed. Run: brew install pandoc", file=sys.stderr)
        return []

    IMAGE_DIR.mkdir(exist_ok=True)

    # Run pandoc to extract media; discard text output (go to /dev/null)
    result = subprocess.run(
        [
            "pandoc",
            "--from=docx",
            "--to=gfm",
            "--wrap=none",
            f"--extract-media={IMAGE_DIR}",
            str(path),
            "--output=/dev/null",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"pandoc error: {result.stderr}", file=sys.stderr)
        return []

    # Collect all media files that pandoc wrote
    saved: list[str] = []
    for f in sorted(IMAGE_DIR.rglob("*")):
        if f.is_file() and f.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}:
            saved.append(str(f))
    return saved


# ────────────────────────────────────────────────────────────────────────
# Audio / video transcription (whisper-cpp + ffmpeg)
# ────────────────────────────────────────────────────────────────────────


def _whisper_binary() -> str | None:
    """Return path to whisper-cpp CLI, or None if not installed.

    The Homebrew formula installs the binary as `whisper-cli`; some builds
    expose `whisper-cpp` or `main`. Try the common names.
    """
    for name in ("whisper-cli", "whisper-cpp", "whisper"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _whisper_model_path() -> str | None:
    """Resolve a ggml model path for whisper-cpp.

    Priority:
      1. $WHISPER_MODEL env var
      2. common Homebrew share path
      3. None — caller must handle absence
    """
    env = os.environ.get(WHISPER_MODEL_ENV)
    if env and Path(env).is_file():
        return env

    candidates = [
        Path.home() / "models" / "ggml-base.bin",
        Path("/opt/homebrew/share/whisper-cpp/ggml-base.bin"),
        Path("/usr/local/share/whisper-cpp/ggml-base.bin"),
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def _run_whisper(audio_path: Path, out_stem: Path) -> Path | None:
    """Invoke whisper-cpp on a 16 kHz WAV file. Returns path to .txt transcript."""
    binary = _whisper_binary()
    if not binary:
        print(
            "whisper-cpp not installed. Run: brew install whisper-cpp",
            file=sys.stderr,
        )
        return None

    model = _whisper_model_path()
    if not model:
        print(
            f"no whisper model found. Set ${WHISPER_MODEL_ENV} to a ggml-*.bin file "
            f"(download from https://huggingface.co/ggerganov/whisper.cpp)",
            file=sys.stderr,
        )
        return None

    cmd = [
        binary,
        "-m", model,
        "-f", str(audio_path),
        "-l", WHISPER_DEFAULT_LANG,
        "-otxt",
        "-of", str(out_stem),     # whisper appends .txt
        "--no-prints",
    ]
    print(f"  running whisper-cpp ({Path(model).name})...", file=sys.stderr)
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"whisper-cpp error: {result.stderr.strip()}", file=sys.stderr)
        return None

    txt = out_stem.with_suffix(out_stem.suffix + ".txt") if out_stem.suffix else Path(str(out_stem) + ".txt")
    if not txt.is_file():
        # whisper-cpp writes "<of>.txt" — handle both shapes
        alt = Path(str(out_stem) + ".txt")
        if alt.is_file():
            txt = alt
        else:
            print(f"whisper-cpp produced no transcript at {txt}", file=sys.stderr)
            return None
    return txt


def _ffmpeg_to_wav(src: Path, dst: Path) -> bool:
    """Convert any audio/video to 16 kHz mono WAV (whisper-cpp's required format)."""
    if not shutil.which("ffmpeg"):
        print("ffmpeg not installed. Run: brew install ffmpeg", file=sys.stderr)
        return False
    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        str(dst),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"ffmpeg error: {result.stderr.strip()[-500:]}", file=sys.stderr)
        return False
    return True


def extract_audio_transcript(path: Path) -> str | None:
    """Audio → 16kHz WAV → whisper-cpp → .txt in _attachments/.

    Returns relative path to the transcript file, or None on failure.
    """
    IMAGE_DIR.mkdir(exist_ok=True)
    stem = _safe_stem(path)
    out_stem = IMAGE_DIR / f"{stem}.transcript"

    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / f"{stem}.wav"
        if not _ffmpeg_to_wav(path, wav):
            return None
        txt = _run_whisper(wav, out_stem)
        if txt is None:
            return None
    print(f"  [audio] transcript: {txt}", file=sys.stderr)
    return str(txt)


def extract_video_transcript(path: Path) -> str | None:
    """Video → ffmpeg extracts audio track → whisper-cpp.

    Same pipeline as audio (ffmpeg handles both transparently).
    """
    return extract_audio_transcript(path)


# ────────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("source", type=Path, help="source file (PDF or DOCX)")
    ap.add_argument("--pages", action="store_true", help="print page count only (PDF)")
    args = ap.parse_args()

    path: Path = args.source
    if not path.is_file():
        print(f"error: not a file: {path}", file=sys.stderr)
        return 1

    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        print(
            f"error: unsupported '{ext}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
            file=sys.stderr,
        )
        return 1

    if args.pages:
        if ext != ".pdf":
            print("--pages is only supported for PDF files", file=sys.stderr)
            return 1
        print(pdf_page_count(path))
        return 0

    print(f"extracting images: {path}", file=sys.stderr)

    n_pages = 0
    large = False
    images: list[str] = []
    transcript: str | None = None

    if ext in PDF_EXTENSIONS:
        n_pages = pdf_page_count(path)
        print(f"  pages: {n_pages}", file=sys.stderr)
        large = n_pages > LARGE_PDF_PAGE_THRESHOLD
        if large:
            print(f"  note: large document — agent will skip restoration step", file=sys.stderr)
        images = extract_pdf_images(path)
    elif ext in DOCX_EXTENSIONS:
        images = extract_docx_images(path)
    elif ext in AUDIO_EXTENSIONS:
        transcript = extract_audio_transcript(path)
    elif ext in VIDEO_EXTENSIONS:
        transcript = extract_video_transcript(path)

    manifest = {
        "source": str(path),
        "format": ext[1:],
        "pages": n_pages,
        "large_doc": large,
        "images": images,
        "transcript": transcript,
    }
    print(json.dumps(manifest, ensure_ascii=False, indent=2))

    if transcript:
        print(f"done: transcript saved to {transcript}", file=sys.stderr)
    else:
        print(f"done: {len(images)} image(s) extracted", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
