#!/usr/bin/env python3
"""Embedding service for llm-wiki.

Generates and stores text embeddings for wiki pages and raw sources.
Used by lint (Layer 1.5) for missing-links and synthesis-drift checks.

Architecture:
    Embedder         — abstract base
    OllamaEmbedder   — Ollama native HTTP API (urllib, no deps)
    OpenAIEmbedder   — OpenAI-compatible (LMStudio, llama.cpp, vLLM, OpenAI)
    EmbedIndex       — manages a JSON file: {name: {hash, vec}}
    update_index()   — re-embed only changed pages, prune stale

Storage:
    wiki/meta/embeddings.json  — wiki page embeddings (key: page name)
    raw/meta/embeddings.json   — raw source embeddings (key: relpath from raw/)

Configuration via env:
    EMBED_PROVIDER  ollama (default) | openai
    EMBED_HOST      default per provider:
                      ollama → http://localhost:11434
                      openai → http://localhost:1234/v1  (LMStudio)
    EMBED_MODEL     default: frida
    EMBED_API_KEY   only needed for OpenAI proper (not for LMStudio)
    EMBED_TIMEOUT   default: 60 seconds

CLI:
    python3 bin/embed.py update                # (re)compute embeddings
    python3 bin/embed.py query "что такое RLHF" # find similar to query text
    python3 bin/embed.py similar RLHF          # find similar to a wiki page
    python3 bin/embed.py stats                 # similarity distribution

Graceful degradation:
    If Ollama is unreachable, raises EmbedderUnavailable. Callers (lint,
    query) should catch this and fall back to non-embedding behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


# ────────────────────────────────────────────────────────────────────────
# Paths and defaults
# ────────────────────────────────────────────────────────────────────────

WIKI_ROOT = Path("wiki")
RAW_ROOT = Path("raw")
WIKI_EMBED_PATH = WIKI_ROOT / "meta" / "embeddings.json"
RAW_EMBED_PATH = RAW_ROOT / "meta" / "embeddings.json"

# Provider selection — "ollama" (native API) or "openai" (OpenAI-compatible:
# LMStudio, llama.cpp server, vLLM, OpenAI itself).
DEFAULT_PROVIDER = os.environ.get("EMBED_PROVIDER", "ollama")
DEFAULT_MODEL = os.environ.get("EMBED_MODEL", "frida")
DEFAULT_TIMEOUT = float(os.environ.get("EMBED_TIMEOUT", "60"))
DEFAULT_API_KEY = os.environ.get("EMBED_API_KEY")  # required only for OpenAI itself

# Per-provider default host. Override with EMBED_HOST.
_PROVIDER_DEFAULT_HOSTS = {
    "ollama": "http://localhost:11434",
    "openai": "http://localhost:1234/v1",  # LMStudio default
}
DEFAULT_HOST = os.environ.get(
    "EMBED_HOST",
    _PROVIDER_DEFAULT_HOSTS.get(DEFAULT_PROVIDER, "http://localhost:11434"),
)


# ────────────────────────────────────────────────────────────────────────
# Errors
# ────────────────────────────────────────────────────────────────────────


class EmbedderError(Exception):
    """Base exception for embedding failures."""


class EmbedderUnavailable(EmbedderError):
    """Provider is unreachable. Callers should fall back gracefully."""


# ────────────────────────────────────────────────────────────────────────
# Embedder interface + Ollama implementation
# ────────────────────────────────────────────────────────────────────────


class Embedder(ABC):
    """Abstract embedding provider. embed(text) → vector."""

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        ...

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        """Default: serial. Subclasses can override for true batching."""
        return [self.embed(t) for t in texts]


class OllamaEmbedder(Embedder):
    """Ollama HTTP API client.

    Uses /api/embed (current) and falls back to /api/embeddings (legacy)
    if 404. No external library deps — just stdlib urllib.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._use_legacy: bool = False

    def embed(self, text: str) -> list[float]:
        if self._use_legacy:
            return self._call_legacy(text)
        try:
            return self._call_new(text)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                self._use_legacy = True
                return self._call_legacy(text)
            raise EmbedderError(f"HTTP {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise EmbedderUnavailable(str(e.reason)) from e
        except (ConnectionError, TimeoutError, OSError) as e:
            raise EmbedderUnavailable(str(e)) from e

    def _post(self, endpoint: str, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.host}{endpoint}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _call_new(self, text: str) -> list[float]:
        data = self._post("/api/embed", {"model": self.model, "input": text})
        if "embeddings" in data and data["embeddings"]:
            return list(data["embeddings"][0])
        if "embedding" in data:
            return list(data["embedding"])
        raise EmbedderError(f"unexpected response shape: {list(data)}")

    def _call_legacy(self, text: str) -> list[float]:
        data = self._post("/api/embeddings", {"model": self.model, "prompt": text})
        if "embedding" not in data:
            raise EmbedderError(f"legacy response missing 'embedding': {list(data)}")
        return list(data["embedding"])


class OpenAIEmbedder(Embedder):
    """OpenAI-compatible embedding API client.

    Works with any server that implements the OpenAI embeddings endpoint:
    - LMStudio:        http://localhost:1234/v1
    - llama.cpp server http://localhost:8080/v1
    - vLLM:            http://localhost:8000/v1
    - OpenAI proper:   https://api.openai.com/v1 (requires api_key)

    The host should include the /v1 prefix — endpoint is appended as
    /embeddings.
    """

    def __init__(
        self,
        host: str = "http://localhost:1234/v1",
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.host = host.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def embed(self, text: str) -> list[float]:
        body = json.dumps({"model": self.model, "input": text}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            f"{self.host}/embeddings",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise EmbedderError(f"HTTP {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise EmbedderUnavailable(str(e.reason)) from e
        except (ConnectionError, TimeoutError, OSError) as e:
            raise EmbedderUnavailable(str(e)) from e

        # OpenAI shape: {"data": [{"embedding": [...], "index": 0}, ...], ...}
        items = data.get("data")
        if isinstance(items, list) and items:
            first = items[0]
            if isinstance(first, dict) and "embedding" in first:
                return list(first["embedding"])
        raise EmbedderError(f"unexpected response shape: {list(data)}")


# ────────────────────────────────────────────────────────────────────────
# Vector math
# ────────────────────────────────────────────────────────────────────────


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity. Returns 0.0 for any zero-norm vector."""
    if len(a) != len(b):
        raise ValueError(f"dim mismatch: {len(a)} vs {len(b)}")
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def vec_mean(vecs: Sequence[Sequence[float]]) -> list[float]:
    """Element-wise mean (centroid). Returns [] for empty input."""
    if not vecs:
        return []
    n = len(vecs)
    dim = len(vecs[0])
    if any(len(v) != dim for v in vecs):
        raise ValueError("inconsistent dimensions")
    return [sum(v[i] for v in vecs) / n for i in range(dim)]


def percentile(values: Sequence[float], p: float) -> float:
    """Linear-interpolated percentile. p ∈ [0, 100]."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * (p / 100)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(sorted_vals[int(k)])
    return float(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f))


# ────────────────────────────────────────────────────────────────────────
# Content hashing & frontmatter stripping
# ────────────────────────────────────────────────────────────────────────


_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)


def strip_frontmatter(text: str) -> str:
    """Remove leading YAML frontmatter for embedding purposes.

    Frontmatter is metadata — embedding it dilutes the semantic signal.
    """
    return _FRONTMATTER_RE.sub("", text, count=1)


def content_hash(text: str) -> str:
    """sha256 of (frontmatter-stripped) content. Stable across whitespace
    in frontmatter, sensitive to body changes."""
    body = strip_frontmatter(text)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ────────────────────────────────────────────────────────────────────────
# EmbedIndex — persistent storage
# ────────────────────────────────────────────────────────────────────────


@dataclass
class EmbedRecord:
    hash: str
    vec: list[float]


class EmbedIndex:
    """Persistent embedding index with hash-based invalidation.

    JSON format:
        {
          "model": "frida",
          "items": {
            "<name>": {"hash": "<sha256>", "vec": [<floats>]}
          }
        }
    """

    def __init__(self, path: Path):
        self.path = path
        self.model: str | None = None
        self.items: dict[str, EmbedRecord] = {}

    def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        self.model = data.get("model")
        for name, rec in data.get("items", {}).items():
            try:
                self.items[name] = EmbedRecord(hash=rec["hash"], vec=rec["vec"])
            except (KeyError, TypeError):
                continue

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "model": self.model,
            "items": {
                name: {"hash": rec.hash, "vec": rec.vec}
                for name, rec in sorted(self.items.items())
            },
        }
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def needs_update(self, name: str, content: str) -> bool:
        h = content_hash(content)
        existing = self.items.get(name)
        return existing is None or existing.hash != h

    def upsert(self, name: str, content: str, vec: list[float]) -> None:
        self.items[name] = EmbedRecord(hash=content_hash(content), vec=list(vec))

    def get(self, name: str) -> list[float] | None:
        rec = self.items.get(name)
        return rec.vec if rec else None

    def remove_stale(self, valid_names: set[str]) -> int:
        """Drop entries whose names aren't in valid_names. Returns count removed."""
        stale = [n for n in self.items if n not in valid_names]
        for n in stale:
            del self.items[n]
        return len(stale)

    def top_k(
        self,
        query_vec: Sequence[float],
        k: int = 10,
        exclude: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Return top-k (name, similarity) pairs in descending order."""
        exclude = exclude or set()
        scored: list[tuple[str, float]] = []
        for name, rec in self.items.items():
            if name in exclude:
                continue
            scored.append((name, cosine(query_vec, rec.vec)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]

    def all_pairwise_similarities(self) -> list[float]:
        """All unordered pair similarities. O(n²) — fine up to a few thousand."""
        names = list(self.items.keys())
        n = len(names)
        sims: list[float] = []
        for i in range(n):
            vi = self.items[names[i]].vec
            for j in range(i + 1, n):
                sims.append(cosine(vi, self.items[names[j]].vec))
        return sims


# ────────────────────────────────────────────────────────────────────────
# Page discovery
# ────────────────────────────────────────────────────────────────────────


def discover_wiki_pages() -> list[tuple[str, str]]:
    """Walk wiki/ → list of (basename, full text). Skips lint reports."""
    pages: list[tuple[str, str]] = []
    if not WIKI_ROOT.is_dir():
        return pages
    for md in sorted(WIKI_ROOT.rglob("*.md")):
        if md.parent.name == "meta" and md.name.startswith("lint-report-"):
            continue
        pages.append((md.stem, md.read_text(encoding="utf-8")))
    return pages


_INDEX_SECTION_HEADINGS = {"Ideas", "Entities", "Questions", "Domains"}
_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s\-:|]+\|\s*$")
_INDEX_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:\|[^\]]*)?\]\]")
# Placeholder for escaped pipes in markdown tables: `\|` is part of cell
# content, not a column separator. We swap before splitting and restore after.
_PIPE_PLACEHOLDER = "__ESCAPED_PIPE__"


def parse_index_summaries(index_path: Path | None = None) -> dict[str, str]:
    """Parse wiki/index.md → {page_name: summary}.

    Each section (## Ideas / ## Entities / ## Questions / ## Domains) holds
    a 2-column table: `| [[Page Name]] | one-line summary |`. We pull
    the first wikilink target from column 1 and the trimmed text from
    column 2.

    Returns empty dict on missing file or parse error — caller handles
    gracefully (summaries are optional metadata).
    """
    if index_path is None:
        index_path = WIKI_ROOT / "index.md"
    if not index_path.is_file():
        return {}

    try:
        text = index_path.read_text(encoding="utf-8")
    except OSError:
        return {}

    # Strip frontmatter
    body = strip_frontmatter(text)

    summaries: dict[str, str] = {}
    in_section = False
    for line in body.split("\n"):
        h = _HEADING_RE.match(line)
        if h:
            in_section = h.group(1).strip() in _INDEX_SECTION_HEADINGS
            continue
        if not in_section:
            continue
        if not line.lstrip().startswith("|"):
            continue
        if _TABLE_SEP_RE.match(line):
            continue
        # Escaped pipes (\|) are content, not separators — swap to placeholder
        # before splitting, restore inside each cell.
        safe = line.replace(r"\|", _PIPE_PLACEHOLDER)
        cells = [
            c.strip().replace(_PIPE_PLACEHOLDER, "|")
            for c in safe.strip().strip("|").split("|")
        ]
        if len(cells) < 2:
            continue
        link_match = _INDEX_WIKILINK_RE.search(cells[0])
        if not link_match:
            continue
        name = link_match.group(1).strip()
        summary = cells[1].strip()
        if name and summary:
            summaries[name] = summary
    return summaries


def wiki_page_paths() -> dict[str, tuple[str, str]]:
    """Map basename → (full_path, folder).

    Used by CLI commands to filter results by folder (skip meta/root files)
    and emit full paths so the caller can Read pages directly.
    folder == "" means wiki root file (cache, summary, log, index).
    """
    result: dict[str, tuple[str, str]] = {}
    if not WIKI_ROOT.is_dir():
        return result
    for md in sorted(WIKI_ROOT.rglob("*.md")):
        if md.parent.name == "meta" and md.name.startswith("lint-report-"):
            continue
        rel = md.relative_to(WIKI_ROOT)
        folder = rel.parts[0] if len(rel.parts) > 1 else ""
        result[md.stem] = (md.as_posix(), folder)
    return result


def discover_raw_pages() -> list[tuple[str, str]]:
    """Walk raw/ → list of (relpath, full text). Excludes raw/formats/ and raw/meta/."""
    pages: list[tuple[str, str]] = []
    if not RAW_ROOT.is_dir():
        return pages
    for md in sorted(RAW_ROOT.rglob("*.md")):
        try:
            rel = md.relative_to(RAW_ROOT)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] in ("formats", "meta"):
            continue
        pages.append((rel.as_posix(), md.read_text(encoding="utf-8")))
    return pages


# ────────────────────────────────────────────────────────────────────────
# Index update
# ────────────────────────────────────────────────────────────────────────


def update_index(
    index: EmbedIndex,
    pages: Sequence[tuple[str, str]],
    embedder: Embedder,
    *,
    model_name: str | None = None,
) -> tuple[int, int]:
    """Update index in place. Returns (updated_count, pruned_count).

    If model_name differs from the cached one, all entries are invalidated
    (different models produce incompatible vectors).
    """
    if model_name and index.model and index.model != model_name:
        index.items.clear()
    if model_name:
        index.model = model_name

    updated = 0
    valid_names: set[str] = set()
    for name, content in pages:
        valid_names.add(name)
        if not index.needs_update(name, content):
            continue
        # Embed the body, not the frontmatter
        body = strip_frontmatter(content)
        vec = embedder.embed(body)
        index.upsert(name, content, vec)
        updated += 1

    pruned = index.remove_stale(valid_names)
    return updated, pruned


# ────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────


def _make_default_embedder() -> Embedder:
    """Build embedder from EMBED_PROVIDER env var (ollama | openai)."""
    if DEFAULT_PROVIDER == "ollama":
        return OllamaEmbedder(host=DEFAULT_HOST, model=DEFAULT_MODEL)
    if DEFAULT_PROVIDER == "openai":
        return OpenAIEmbedder(
            host=DEFAULT_HOST,
            model=DEFAULT_MODEL,
            api_key=DEFAULT_API_KEY,
        )
    raise ValueError(
        f"unknown EMBED_PROVIDER={DEFAULT_PROVIDER!r}. "
        f"Expected one of: ollama, openai"
    )


def cmd_update(_args) -> int:
    embedder = _make_default_embedder()

    wiki_pages = discover_wiki_pages()
    raw_pages = discover_raw_pages()
    print(
        f"discovered: {len(wiki_pages)} wiki pages, {len(raw_pages)} raw pages",
        file=sys.stderr,
    )

    wiki_idx = EmbedIndex(WIKI_EMBED_PATH)
    wiki_idx.load()
    raw_idx = EmbedIndex(RAW_EMBED_PATH)
    raw_idx.load()

    try:
        wu, wp = update_index(wiki_idx, wiki_pages, embedder, model_name=DEFAULT_MODEL)
        ru, rp = update_index(raw_idx, raw_pages, embedder, model_name=DEFAULT_MODEL)
    except EmbedderUnavailable as e:
        print(f"embedder unavailable: {e}", file=sys.stderr)
        print(f"  hint: is Ollama running at {DEFAULT_HOST}?", file=sys.stderr)
        return 2
    except EmbedderError as e:
        print(f"embedder error: {e}", file=sys.stderr)
        return 2

    wiki_idx.save()
    raw_idx.save()

    print(f"wiki: {wu} updated, {wp} pruned ({len(wiki_idx.items)} total)")
    print(f"raw:  {ru} updated, {rp} pruned ({len(raw_idx.items)} total)")
    return 0


def _filter_and_format_results(
    results: list[tuple[str, float]],
    paths: dict[str, tuple[str, str]],
    k: int,
    include_meta: bool = False,
    summaries: dict[str, str] | None = None,
) -> list[tuple[str, float, str, str]]:
    """Filter top_k results by content folder, attach paths and summaries.

    Returns list of (name, similarity, full_path, summary) tuples, length <= k.
    summary is "" if not present in index.md.
    Stale embeddings (page no longer exists) are dropped silently.
    Meta pages and wiki root files (cache/summary/log/index) are excluded
    unless include_meta=True.
    """
    summaries = summaries or {}
    out: list[tuple[str, float, str, str]] = []
    for name, sim in results:
        info = paths.get(name)
        if info is None:
            continue  # stale embedding — page deleted
        path, folder = info
        if not include_meta and folder in ("", "meta"):
            continue
        out.append((name, sim, path, summaries.get(name, "")))
        if len(out) >= k:
            break
    return out


def _format_result_line(sim: float, path: str, summary: str) -> str:
    """One result line: '+0.621  wiki/ideas/RLHF.md  — summary text'.
    No summary → no trailing dash."""
    base = f"  {sim:+.3f}  {path}"
    return f"{base}  — {summary}" if summary else base


def cmd_query(args) -> int:
    embedder = _make_default_embedder()
    wiki_idx = EmbedIndex(WIKI_EMBED_PATH)
    wiki_idx.load()
    if not wiki_idx.items:
        print("wiki index is empty. run: embed.py update", file=sys.stderr)
        return 1
    paths = wiki_page_paths()
    summaries = parse_index_summaries()
    try:
        q_vec = embedder.embed(args.text)
    except EmbedderUnavailable as e:
        print(f"embedder unavailable: {e}", file=sys.stderr)
        return 2
    raw = wiki_idx.top_k(q_vec, k=args.k * 3 if not args.all else args.k)
    filtered = _filter_and_format_results(
        raw, paths, args.k, include_meta=args.all, summaries=summaries,
    )
    for _name, sim, path, summary in filtered:
        print(_format_result_line(sim, path, summary))
    return 0


def cmd_similar(args) -> int:
    wiki_idx = EmbedIndex(WIKI_EMBED_PATH)
    wiki_idx.load()
    vec = wiki_idx.get(args.page)
    if vec is None:
        print(f"page not in index: {args.page}", file=sys.stderr)
        print("  hint: run 'embed.py update' first", file=sys.stderr)
        return 1
    paths = wiki_page_paths()
    summaries = parse_index_summaries()
    raw = wiki_idx.top_k(
        vec,
        k=args.k * 3 if not args.all else args.k,
        exclude={args.page},
    )
    filtered = _filter_and_format_results(
        raw, paths, args.k, include_meta=args.all, summaries=summaries,
    )
    for _name, sim, path, summary in filtered:
        print(_format_result_line(sim, path, summary))
    return 0


def cmd_stats(_args) -> int:
    wiki_idx = EmbedIndex(WIKI_EMBED_PATH)
    wiki_idx.load()
    if not wiki_idx.items:
        print("wiki index is empty. run: embed.py update", file=sys.stderr)
        return 1
    sims = wiki_idx.all_pairwise_similarities()
    if not sims:
        print(f"pages: {len(wiki_idx.items)} (need ≥2 for pairwise stats)")
        return 0
    print(f"pages:  {len(wiki_idx.items)}")
    print(f"pairs:  {len(sims)}")
    print(f"min:    {min(sims):+.3f}")
    print(f"p25:    {percentile(sims, 25):+.3f}")
    print(f"median: {percentile(sims, 50):+.3f}")
    print(f"p75:    {percentile(sims, 75):+.3f}")
    print(f"p95:    {percentile(sims, 95):+.3f}")
    print(f"p99:    {percentile(sims, 99):+.3f}")
    print(f"max:    {max(sims):+.3f}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd")

    p_update = sub.add_parser("update", help="(re)compute embeddings for changed pages")
    p_update.set_defaults(func=cmd_update)

    p_query = sub.add_parser("query", help="find pages similar to query text")
    p_query.add_argument("text")
    p_query.add_argument("-k", type=int, default=10)
    p_query.add_argument("--all", action="store_true",
                         help="include meta pages and wiki root files in results")
    p_query.set_defaults(func=cmd_query)

    p_similar = sub.add_parser("similar", help="find pages similar to a wiki page")
    p_similar.add_argument("page")
    p_similar.add_argument("-k", type=int, default=10)
    p_similar.add_argument("--all", action="store_true",
                           help="include meta pages and wiki root files in results")
    p_similar.set_defaults(func=cmd_similar)

    p_stats = sub.add_parser("stats", help="similarity distribution (for threshold calibration)")
    p_stats.set_defaults(func=cmd_stats)

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
