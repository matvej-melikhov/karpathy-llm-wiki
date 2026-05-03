#!/usr/bin/env python3
"""Programmatic lint for the wiki.

Implements deterministic checks that don't need LLM judgment. Outputs a
structured `wiki/meta/lint-state.json` with `open_issues`. The semantic
checks (contradiction, outdated-claim, missing-concept, style-nit) stay
agent-driven — they're done in a separate LLM-fueled pass via the lint
skill (Phase 8 of ingest).

This script is the "first layer" of two-layer lint:
1. bin/lint.py — fast, deterministic, ~all schema/structural checks
2. lint skill — LLM pass for semantic checks, reads lint-state.json
   from us, adds its own `open_issues` entries.

Usage:
    python3 bin/lint.py            # run, write lint-state.json, print summary
    python3 bin/lint.py --force    # bypass aggregate-hash skip-check
    python3 bin/lint.py --json     # also print full open_issues JSON
    python3 bin/lint.py --check <type>   # run only one check (debugging)

Exit codes:
    0 — clean (no issues, or skipped)
    1 — issues found (just informational; not a hard failure)
    2 — internal error
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


# ────────────────────────────────────────────────────────────────────────
# Paths and constants
# ────────────────────────────────────────────────────────────────────────

WIKI_ROOT = Path("wiki")
LINT_STATE_PATH = WIKI_ROOT / "meta" / "lint-state.json"

RAW_ROOT = Path("raw")
RAW_FORMATS_DIR = RAW_ROOT / "formats"

# Binary extensions that should live in raw/formats/, not raw/ root
BINARY_SOURCE_EXTENSIONS = {
    ".pdf", ".docx", ".doc",
    ".mp3", ".wav", ".m4a", ".ogg", ".flac",
    ".mp4", ".mov", ".avi", ".mkv", ".webm",
    ".pptx", ".ppt", ".xlsx", ".xls",
}

# Folder ↔ frontmatter `type:` mapping. Folder is source of truth.
FOLDER_TO_TYPE = {
    "ideas": "idea",
    "entities": "entity",
    "questions": "question",
    "domains": "domain",
}
CONTENT_FOLDERS = list(FOLDER_TO_TYPE.keys())

VALID_STATUSES = {"evaluation", "in-progress", "ready"}
LEGACY_FIELDS = {"title", "complexity", "first_mentioned"}

# Tag casing rules: known abbreviations stay uppercase, regular words get
# Capitalized first letter. We can't enumerate all valid abbreviations, so
# we use heuristics.
KNOWN_ABBREVIATIONS = {
    "ML", "RL", "NLP", "RLHF", "LLM", "LoRA", "GAN", "VAE", "MOC",
    "AI", "DL", "CV", "NN", "GPT", "BERT", "API", "URL", "HTML",
    "JSON", "YAML", "PDF", "PPO", "DPO", "RM", "KL", "TD", "MC",
    "GAE", "TRPO", "MAP", "MRR", "DCG", "ERR", "IR", "QA", "GBDT",
    "MDI", "FI", "PFound", "OCR",
}


# ────────────────────────────────────────────────────────────────────────
# Frontmatter parser
# ────────────────────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class Frontmatter:
    """Parsed frontmatter. Keeps raw text for round-trip preservation."""
    raw: str                     # text between --- markers, no markers
    fields: dict[str, Any]       # parsed values: str | list | None
    inline_lists: set[str]       # names of fields written as `[a, b]` inline
    end_line: int                # line number where second --- closes (0-indexed)


def _parse_yaml_subset(text: str) -> tuple[dict[str, Any], set[str]]:
    """Parse a restricted subset of YAML matching our frontmatter schema:
    - top-level keys with string or list values
    - lists either block-style (newline + `  - item`) or inline `[a, b]`
    - quoted strings: `"..."` or `'...'`
    - bare strings until end of line

    Returns (fields, inline_list_field_names).

    Limitations: no nested structures, no multiline strings, no anchors.
    Our schema doesn't use any of these.
    """
    fields: dict[str, Any] = {}
    inline_lists: set[str] = set()

    lines = text.split("\n")
    i = 0
    current_key: str | None = None
    current_list: list[str] | None = None

    while i < len(lines):
        line = lines[i]
        stripped = line.rstrip()

        # Skip blank lines and comments
        if not stripped or stripped.lstrip().startswith("#"):
            i += 1
            continue

        # Block-list item: starts with whitespace + `-`
        list_item_match = re.match(r"^\s+-\s*(.*)$", line)
        if list_item_match and current_list is not None:
            value = list_item_match.group(1).strip()
            current_list.append(_strip_quotes(value))
            i += 1
            continue

        # If we hit a non-list line while collecting a list, finalize it
        if current_key is not None and current_list is not None:
            fields[current_key] = current_list
            current_key = None
            current_list = None

        # Top-level key: value
        kv_match = re.match(r"^([a-z_][a-z_0-9]*)\s*:\s*(.*)$", line, re.IGNORECASE)
        if not kv_match:
            i += 1
            continue
        key = kv_match.group(1)
        value_str = kv_match.group(2).strip()

        if value_str == "":
            # Block list or null — peek ahead to see
            if i + 1 < len(lines) and re.match(r"^\s+-\s*", lines[i + 1]):
                current_key = key
                current_list = []
            else:
                fields[key] = None
            i += 1
            continue

        if value_str.startswith("[") and value_str.endswith("]"):
            # Inline list
            inline_content = value_str[1:-1].strip()
            if not inline_content:
                fields[key] = []
            else:
                items = [_strip_quotes(x.strip()) for x in inline_content.split(",")]
                fields[key] = items
            inline_lists.add(key)
            i += 1
            continue

        # Scalar string
        fields[key] = _strip_quotes(value_str)
        i += 1

    # Finalize trailing list
    if current_key is not None and current_list is not None:
        fields[current_key] = current_list

    return fields, inline_lists


def _strip_quotes(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def parse_frontmatter(text: str) -> tuple[Frontmatter | None, str]:
    """Split a markdown file into (Frontmatter, body). Returns (None, text)
    if no frontmatter."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None, text
    raw = m.group(1)
    fields, inline_lists = _parse_yaml_subset(raw)
    end_line = text[: m.end()].count("\n")
    body = text[m.end() :]
    return Frontmatter(raw=raw, fields=fields, inline_lists=inline_lists, end_line=end_line), body


# ────────────────────────────────────────────────────────────────────────
# Page model
# ────────────────────────────────────────────────────────────────────────


@dataclass
class Page:
    """One markdown page in the wiki."""
    path: Path                     # relative path from project root, e.g. "wiki/ideas/RLHF.md"
    folder: str                    # one of CONTENT_FOLDERS, or "meta", or "" for root
    name: str                      # filename without .md extension
    text: str                      # full content
    fm: Frontmatter | None         # parsed frontmatter (None if absent)
    body: str                      # text after frontmatter

    @property
    def page_type(self) -> str | None:
        """Value of `type:` field, or None."""
        if self.fm is None:
            return None
        return self.fm.fields.get("type")

    def relpath(self) -> str:
        """Path as POSIX string for issue reporting."""
        return self.path.as_posix()


# ────────────────────────────────────────────────────────────────────────
# Vault loading
# ────────────────────────────────────────────────────────────────────────


def discover_pages() -> list[Page]:
    """Walk wiki/ and return all .md pages. Excludes wiki/meta/ files
    (they're infrastructure, not content)."""
    pages: list[Page] = []
    if not WIKI_ROOT.is_dir():
        return pages

    for md in sorted(WIKI_ROOT.rglob("*.md")):
        # skip lint reports
        if md.parent.name == "meta" and md.name.startswith("lint-report-"):
            continue
        # determine folder
        rel = md.relative_to(WIKI_ROOT)
        if len(rel.parts) == 1:
            folder = ""
        else:
            folder = rel.parts[0]

        text = md.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)
        pages.append(Page(
            path=md,
            folder=folder,
            name=md.stem,
            text=text,
            fm=fm,
            body=body,
        ))
    return pages


# ────────────────────────────────────────────────────────────────────────
# Skip-check via aggregate hash
# ────────────────────────────────────────────────────────────────────────


def compute_wiki_hash(pages: list[Page]) -> str:
    """Aggregate sha256 over all wiki pages' content.

    Sort by path to make order-independent. Concatenate contents. One hash.
    Excludes lint-state.json itself and lint-report files (they're outputs).
    """
    h = hashlib.sha256()
    for p in sorted(pages, key=lambda p: p.path.as_posix()):
        h.update(p.path.as_posix().encode("utf-8"))
        h.update(b"\n")
        h.update(p.text.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def load_lint_state() -> dict[str, Any]:
    if not LINT_STATE_PATH.exists():
        return {}
    try:
        return json.loads(LINT_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_lint_state(state: dict[str, Any]) -> None:
    LINT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    LINT_STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# ────────────────────────────────────────────────────────────────────────
# Issue collection
# ────────────────────────────────────────────────────────────────────────


@dataclass
class Issue:
    """A single open issue to report."""
    type: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, **self.payload}


# ────────────────────────────────────────────────────────────────────────
# Checks
# ────────────────────────────────────────────────────────────────────────
#
# Each check function takes a list[Page] (and optionally pre-built indexes)
# and yields Issue objects. Checks are pure — no I/O.
# ────────────────────────────────────────────────────────────────────────


def check_status_not_in_enum(pages: list[Page]) -> Iterable[Issue]:
    """status: must be one of evaluation/in-progress/ready (when present and
    not on entity)."""
    for p in pages:
        if p.fm is None:
            continue
        status = p.fm.fields.get("status")
        if status is None:
            continue
        # entity has its own check that says status shouldn't even be there
        if p.page_type == "entity":
            continue
        if status not in VALID_STATUSES:
            yield Issue("status-not-in-enum", {
                "where": p.relpath(),
                "value": status,
                "fix": "in-progress",
            })


def check_status_on_entity(pages: list[Page]) -> Iterable[Issue]:
    """type: entity pages must not have a status field."""
    for p in pages:
        if p.fm is None:
            continue
        if p.page_type != "entity":
            continue
        if "status" in p.fm.fields:
            yield Issue("status-on-entity", {"where": p.relpath()})


def check_legacy_field(pages: list[Page]) -> Iterable[Issue]:
    """Old-schema fields (title/complexity/first_mentioned) on non-meta pages."""
    for p in pages:
        if p.fm is None:
            continue
        if p.page_type == "meta":
            continue
        for field_name in LEGACY_FIELDS:
            if field_name in p.fm.fields:
                yield Issue("legacy-field", {
                    "where": p.relpath(),
                    "field": field_name,
                })


def _expected_tag_casing(tag: str) -> str | None:
    """Return the canonically-cased version of `tag`, or None if it already
    matches schema rules.

    Schema (from references/frontmatter.md):
    - abbreviations uppercase: ML, RL, NLP, RLHF, ...
    - regular words capitalized: Alignment, Optimization, ...
    - no lowercase abbreviations, no mixed-case abbreviations

    Heuristic (without hardcoding every possible abbreviation):
    - tag is all-uppercase → already canonical abbreviation, accept
    - tag in KNOWN_ABBREVIATIONS list (case-insensitive) → fix to canonical form
      (handles LoRA, GPT-3, etc. with non-trivial casing)
    - tag starts with uppercase letter (rest mixed/lower) → accept
      (Capitalized "Alignment", or mixed like "KMeans", "MapReduce")
    - tag starts with lowercase → fix: capitalize first letter, lowercase rest

    This is permissive: any tag that *could* be a valid abbreviation or
    capitalized word passes. It only flags clear violations like 'ml' or
    'optimization'.
    """
    if not tag:
        return None

    # All-uppercase: canonical abbreviation form
    if tag.isupper():
        return None

    # Known abbreviation with non-trivial casing (LoRA → keep as LoRA)
    upper = tag.upper()
    for a in KNOWN_ABBREVIATIONS:
        if a.upper() == upper:
            return None if tag == a else a

    # Starts with uppercase letter: accept (Capitalized regular word, or
    # mixed-case like KMeans we can't validate further without a dictionary)
    if tag[0].isupper():
        return None

    # Starts with lowercase: violation. Suggest Capitalized form as fix.
    expected = tag[0].upper() + tag[1:].lower()
    return expected


def check_lowercase_tags(pages: list[Page]) -> Iterable[Issue]:
    """Tags must follow casing schema: abbreviations uppercase
    (ML/RL/NLP), regular words Capitalized."""
    for p in pages:
        if p.fm is None:
            continue
        tags = p.fm.fields.get("tags")
        if not tags or not isinstance(tags, list):
            continue
        bad: list[str] = []
        for t in tags:
            if not isinstance(t, str):
                continue
            if _expected_tag_casing(t) is not None:
                bad.append(t)
        if bad:
            yield Issue("lowercase-tags", {
                "where": p.relpath(),
                "tags": bad,
            })


def check_inline_tags(pages: list[Page]) -> Iterable[Issue]:
    """tags: [a, b] inline format instead of block-style YAML.

    Empty inline lists (tags: []) are accepted — that's a normal placeholder
    in templates. We only flag inline lists with at least one element."""
    for p in pages:
        if p.fm is None:
            continue
        if "tags" not in p.fm.inline_lists:
            continue
        tags = p.fm.fields.get("tags")
        if isinstance(tags, list) and len(tags) > 0:
            yield Issue("inline-tags", {"where": p.relpath()})


def check_raw_link_with_extension(pages: list[Page]) -> Iterable[Issue]:
    """[[raw/X.md]] in `sources:` should be [[raw/X]] (no extension).

    Compound-extension transcripts like [[raw/X.docx.md]] are NOT flagged:
    the .md is necessary because [[raw/X.docx]] would resolve to the
    original DOCX (not the markdown transcript). We only flag .md when
    the basename has no other dots before the .md.
    """
    for p in pages:
        if p.fm is None:
            continue
        sources = p.fm.fields.get("sources")
        if not sources or not isinstance(sources, list):
            continue
        for src in sources:
            if not isinstance(src, str):
                continue
            m = re.fullmatch(r"\[\[(raw/[^\]|]+)\.md(\|[^\]]+)?\]\]", src)
            if not m:
                continue
            inner = m.group(1)  # everything between [[raw/ and .md
            # If the basename (last segment) contains a dot, we're dealing
            # with a compound extension like X.docx.md — keep the .md.
            basename = inner.rsplit("/", 1)[-1]
            if "." in basename:
                continue
            yield Issue("raw-link-with-extension", {
                "where": p.relpath(),
                "link": src,
            })


_BODY_RAW_REF_RE = re.compile(r"\[\[raw/[^\]]+\]\]")


def check_raw_ref_in_body(pages: list[Page]) -> Iterable[Issue]:
    """[[raw/...]] mentioned in page body (not just frontmatter).

    Skips meta pages (log.md, cache.md, summary.md, lint-report-*) — they
    legitimately mention raw paths as documentation of operations.
    """
    for p in pages:
        if p.fm is None or not p.body:
            continue
        if p.page_type == "meta":
            continue
        if p.folder == "meta":
            continue
        for line_no, line in enumerate(p.body.split("\n"), start=1):
            for m in _BODY_RAW_REF_RE.finditer(line):
                link = m.group(0)
                yield Issue("raw-ref-in-body", {
                    "where": p.relpath(),
                    "link": link,
                    "line": line_no,
                })


# ────────────────────────────────────────────────────────────────────────
# Wikilink graph
# ────────────────────────────────────────────────────────────────────────

# Match wikilinks: [[Target]] or [[Target|Alias]] or [[Target#section]] or
# [[Target#section|alias]]. Excluded: image/file embeds (![[...]]) — these are
# Obsidian's transclusion syntax, not navigation.
#
# The basename match `[^\]|#]+` stops at a real `|` (alias separator) or `]`
# or `#` (section anchor). Inside markdown tables `|` may be escaped as `\|`
# — _normalize_wikilink_text replaces those before matching so the basename
# isn't truncated at the escape.
_WIKILINK_RE = re.compile(r"(?<!\!)\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")


def _normalize_wikilink_text(text: str) -> str:
    """Pre-process for wikilink extraction:
    - replace `\\|` (escaped pipe in markdown tables) with `|`
    - strip fenced code blocks ```...```
    - strip inline code `...`
    """
    cleaned = text.replace(r"\|", "|")
    cleaned = re.sub(r"```.*?```", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"`[^`\n]*`", "", cleaned)
    return cleaned


def _extract_wikilinks(text: str) -> list[str]:
    """Return target names of all wikilinks in text."""
    cleaned = _normalize_wikilink_text(text)
    return [m.group(1).strip() for m in _WIKILINK_RE.finditer(cleaned)]


def _build_link_graph(pages: list[Page]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Return (outbound, inbound) maps keyed by page name (basename without
    extension). Body wikilinks only — not frontmatter `related`/`domain`/etc.
    Wait: actually `related` field IS part of inbound counting (otherwise
    pages with only frontmatter links look like orphans). Same for `domain`.
    But `sources` (raw refs) shouldn't count.
    """
    # Build a name → page map
    by_name: dict[str, Page] = {p.name: p for p in pages}

    outbound: dict[str, set[str]] = {p.name: set() for p in pages}
    inbound: dict[str, set[str]] = {p.name: set() for p in pages}

    for p in pages:
        # Body links
        body_links = _extract_wikilinks(p.body)
        # Frontmatter wikilinks from related, domain, sources
        fm_links: list[str] = []
        if p.fm is not None:
            for fld in ("related", "domain"):
                values = p.fm.fields.get(fld)
                if isinstance(values, list):
                    for v in values:
                        if isinstance(v, str):
                            fm_links.extend(_extract_wikilinks(v))

        for target in body_links + fm_links:
            # Skip raw-refs — they're not wiki page links
            if target.startswith("raw/") or target.startswith("raw\\"):
                continue
            outbound[p.name].add(target)
            if target in by_name:
                inbound[target].add(p.name)

    return outbound, inbound


def check_dead_link(pages: list[Page]) -> Iterable[Issue]:
    """Wikilink to a non-existent page (in body, related, or domain field).

    Skips:
    - links inside fenced code blocks (```)
    - links inside inline code spans (`...`)
    - escaped pipes in markdown tables are normalized before matching
    - meta pages and lint-report files (their bodies are documentation of
      links that may not exist as pages)
    """
    by_name = {p.name: p for p in pages}
    seen: set[tuple[str, str]] = set()
    for p in pages:
        # Skip meta pages — their bodies legitimately reference operations
        # logs, lint reports, etc.
        if p.page_type == "meta" or p.folder == "meta":
            continue

        # Walk lines tracking fenced code-block state
        in_code = False
        for line_no, raw_line in enumerate(p.body.split("\n"), start=1):
            stripped = raw_line.lstrip()
            if stripped.startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                continue
            # Normalize: strip inline code, replace escaped pipes
            line = re.sub(r"`[^`\n]*`", "", raw_line).replace(r"\|", "|")
            for m in _WIKILINK_RE.finditer(line):
                target = m.group(1).strip()
                if target.startswith("raw/"):
                    continue
                if target in by_name:
                    continue
                key = (p.relpath(), target)
                if key in seen:
                    continue
                seen.add(key)
                yield Issue("dead-link", {
                    "where": p.relpath(),
                    "what": f"[[{target}]]",
                    "context": f"line {line_no}",
                })

        # Frontmatter related / domain
        if p.fm is None:
            continue
        for fld in ("related", "domain"):
            values = p.fm.fields.get(fld)
            if not isinstance(values, list):
                continue
            for v in values:
                if not isinstance(v, str):
                    continue
                for target in _extract_wikilinks(v):
                    if target.startswith("raw/"):
                        continue
                    if target in by_name:
                        continue
                    key = (p.relpath(), target)
                    if key in seen:
                        continue
                    seen.add(key)
                    yield Issue("dead-link", {
                        "where": p.relpath(),
                        "what": f"[[{target}]]",
                        "context": f"frontmatter {fld}",
                    })


def check_orphan(pages: list[Page]) -> Iterable[Issue]:
    """Page with zero inbound wikilinks. Excludes meta pages (they're
    infrastructure, not part of the knowledge graph) and the wiki root files
    (index/log/cache/summary)."""
    _, inbound = _build_link_graph(pages)
    for p in pages:
        if p.page_type == "meta":
            continue
        # also skip wiki root files even if they're not type:meta
        if p.folder == "":
            continue
        if not inbound.get(p.name):
            yield Issue("orphan", {"where": p.relpath()})


def check_asymmetric_related(pages: list[Page]) -> Iterable[Issue]:
    """A→B in related: but B→A missing. Reported once per pair (alphabetical)."""
    by_name = {p.name: p for p in pages}

    related_map: dict[str, set[str]] = {}
    for p in pages:
        if p.fm is None:
            continue
        related = p.fm.fields.get("related")
        if not isinstance(related, list):
            continue
        targets: set[str] = set()
        for v in related:
            if not isinstance(v, str):
                continue
            for m in _WIKILINK_RE.finditer(v):
                t = m.group(1).strip()
                if not t.startswith("raw/"):
                    targets.add(t)
        related_map[p.name] = targets

    seen: set[tuple[str, str]] = set()
    for a, targets in related_map.items():
        for b in targets:
            if b not in by_name:
                continue  # dead-link case, separate check
            b_targets = related_map.get(b, set())
            if a in b_targets:
                continue  # symmetric
            # Asymmetric. Report once per unordered pair (alphabetical key).
            key = tuple(sorted([a, b]))
            if key in seen:
                continue
            seen.add(key)
            page_a, page_b = by_name[a], by_name[b]
            yield Issue("asymmetric-related", {
                "page_a": page_a.relpath(),
                "page_b": page_b.relpath(),
                "page_a_type": page_a.page_type,
                "page_b_type": page_b.page_type,
            })


# ────────────────────────────────────────────────────────────────────────
# Index analysis
# ────────────────────────────────────────────────────────────────────────


# Map index-section heading → folder containing those pages.
_INDEX_SECTION_TO_FOLDER = {
    "Ideas": "ideas",
    "Entities": "entities",
    "Questions": "questions",
    "Domains": "domains",
}


def _parse_index_tables(index_text: str) -> dict[str, set[str]]:
    """Parse wiki/index.md into {section_name: set of wikilink targets}.

    Sections are detected by `## <SectionName>` headings matching keys in
    _INDEX_SECTION_TO_FOLDER. Within each section we look for table rows
    where the first cell contains a wikilink.
    """
    result: dict[str, set[str]] = {}
    current_section: str | None = None

    for line in index_text.split("\n"):
        # Heading
        h = re.match(r"^##\s+(.+?)\s*$", line)
        if h:
            heading = h.group(1).strip()
            if heading in _INDEX_SECTION_TO_FOLDER:
                current_section = heading
                result.setdefault(current_section, set())
            else:
                current_section = None
            continue
        if current_section is None:
            continue
        # Table row: starts with `|`
        if not line.lstrip().startswith("|"):
            continue
        # Skip separator row: `|---|---|`
        if re.match(r"^\s*\|[\s\-:|]+\|\s*$", line):
            continue
        # Extract first cell content
        cells = [c.strip() for c in line.split("|") if c.strip()]
        if not cells:
            continue
        first_cell = cells[0]
        for m in _WIKILINK_RE.finditer(first_cell):
            result[current_section].add(m.group(1).strip())
    return result


def check_stale_index_entry(pages: list[Page]) -> Iterable[Issue]:
    """Row in wiki/index.md table whose wikilink resolves to no existing page."""
    index_path = WIKI_ROOT / "index.md"
    if not index_path.is_file():
        return
    fm, body = parse_frontmatter(index_path.read_text(encoding="utf-8"))
    by_name = {p.name: p for p in pages}

    sections = _parse_index_tables(body)
    for section, targets in sections.items():
        for target in sorted(targets):
            if target.startswith("raw/"):
                continue
            if target not in by_name:
                yield Issue("stale-index-entry", {
                    "link": f"[[{target}]]",
                    "section": section,
                })


def check_missing_index_entry(pages: list[Page]) -> Iterable[Issue]:
    """Content page exists but no row in wiki/index.md table."""
    index_path = WIKI_ROOT / "index.md"
    if not index_path.is_file():
        return
    _, body = parse_frontmatter(index_path.read_text(encoding="utf-8"))
    sections = _parse_index_tables(body)

    # Flatten: set of all names referenced in any section
    indexed_names: set[str] = set()
    for targets in sections.values():
        indexed_names.update(targets)

    for p in pages:
        if p.folder not in CONTENT_FOLDERS:
            continue
        if p.name in indexed_names:
            continue
        # Page exists but missing from index
        page_type = p.page_type or FOLDER_TO_TYPE.get(p.folder, "")
        yield Issue("missing-index-entry", {
            "where": p.relpath(),
            "page_type": page_type,
        })


# ────────────────────────────────────────────────────────────────────────
# Body-structure checks
# ────────────────────────────────────────────────────────────────────────


# ────────────────────────────────────────────────────────────────────────
# raw/ structure checks (scans raw/, not wiki/)
# ────────────────────────────────────────────────────────────────────────


def check_binary_source_outside_formats(pages: list[Page]) -> Iterable[Issue]:
    """Binary source files (PDF, DOCX, audio, video) should live in
    raw/formats/, not in raw/ root or other raw/ subdirectories.

    The agent (transcribe skill) moves them on next /transcribe call, so
    this is in ask-category — user confirms before the move.
    """
    if not RAW_ROOT.is_dir():
        return
    for f in RAW_ROOT.rglob("*"):
        if not f.is_file():
            continue
        try:
            rel = f.relative_to(RAW_ROOT)
        except ValueError:
            continue
        # Skip files already in raw/formats/ or raw/meta/
        if rel.parts[0] in ("formats", "meta"):
            continue
        if f.suffix.lower() in BINARY_SOURCE_EXTENSIONS:
            yield Issue("binary-source-outside-formats", {
                "where": f.as_posix(),
                "suggested": str(RAW_FORMATS_DIR / f.name),
            })


def check_empty_section(pages: list[Page]) -> Iterable[Issue]:
    """Heading (## or higher) followed by no non-empty content before the
    next heading or end of file. Skip-category — informational only, since
    empty sections may be intentional placeholders."""
    for p in pages:
        if p.fm is None or not p.body:
            continue
        # Skip meta pages (their structure is operation-driven, not content)
        if p.page_type == "meta" or p.folder == "meta":
            continue

        lines = p.body.split("\n")
        in_code = False
        # Walk: when we find a heading, look ahead for content until next heading
        for i, raw_line in enumerate(lines):
            stripped = raw_line.lstrip()
            if stripped.startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                continue
            h = re.match(r"^(#{2,})\s+(.+?)\s*$", raw_line)
            if not h:
                continue
            heading_level = len(h.group(1))
            heading_text = h.group(2).strip()
            # Look ahead for content
            has_content = False
            j = i + 1
            in_code_inner = False
            while j < len(lines):
                next_line = lines[j]
                ns = next_line.lstrip()
                if ns.startswith("```"):
                    in_code_inner = not in_code_inner
                    j += 1
                    continue
                # Next heading at same or higher level → section ended
                next_h = re.match(r"^(#{1,})\s+", next_line)
                if next_h and not in_code_inner:
                    next_level = len(next_h.group(1))
                    if next_level <= heading_level:
                        break
                if next_line.strip():
                    # Non-empty line. Skip HTML comments — they're
                    # placeholders, not content.
                    if not next_line.strip().startswith("<!--"):
                        has_content = True
                        break
                j += 1
            if not has_content:
                yield Issue("empty-section", {
                    "where": p.relpath(),
                    "section": heading_text,
                })


def check_dangling_domain_ref(pages: list[Page]) -> Iterable[Issue]:
    """domain: ["[[X]]"] points to a non-existent wiki/domains/X.md."""
    domain_pages = {p.name for p in pages if p.folder == "domains"}
    for p in pages:
        if p.fm is None:
            continue
        domains = p.fm.fields.get("domain")
        if not isinstance(domains, list):
            continue
        for v in domains:
            if not isinstance(v, str):
                continue
            for m in _WIKILINK_RE.finditer(v):
                target = m.group(1).strip()
                if target.startswith("raw/"):
                    continue
                if target not in domain_pages:
                    yield Issue("dangling-domain-ref", {
                        "where": p.relpath(),
                        "missing_domain": target,
                    })


def check_folder_type_mismatch(pages: list[Page]) -> Iterable[Issue]:
    """Page in wiki/<X>/ must have type: matching the folder."""
    for p in pages:
        if p.fm is None:
            continue
        expected = FOLDER_TO_TYPE.get(p.folder)
        if expected is None:
            continue  # not in a content folder (e.g. meta or wiki root)
        current = p.page_type
        if current != expected:
            yield Issue("folder-type-mismatch", {
                "where": p.relpath(),
                "current_type": current,
                "expected_type": expected,
            })


# Registry: ordered list of (issue_type_string, check_function)
_CHECKS: list[tuple[str, Any]] = [
    ("status-not-in-enum", check_status_not_in_enum),
    ("status-on-entity", check_status_on_entity),
    ("legacy-field", check_legacy_field),
    ("lowercase-tags", check_lowercase_tags),
    ("inline-tags", check_inline_tags),
    ("folder-type-mismatch", check_folder_type_mismatch),
    ("raw-link-with-extension", check_raw_link_with_extension),
    ("raw-ref-in-body", check_raw_ref_in_body),
    ("dead-link", check_dead_link),
    ("orphan", check_orphan),
    ("asymmetric-related", check_asymmetric_related),
    ("dangling-domain-ref", check_dangling_domain_ref),
    ("stale-index-entry", check_stale_index_entry),
    ("missing-index-entry", check_missing_index_entry),
    ("empty-section", check_empty_section),
    ("binary-source-outside-formats", check_binary_source_outside_formats),
]


# ────────────────────────────────────────────────────────────────────────
# Layer 1.5: embedding-based checks (--approx mode)
# ────────────────────────────────────────────────────────────────────────
#
# These checks need a pre-computed embedding index (run bin/embed.py first).
# They don't call the embedder themselves — pure consumers of stored vectors.
# Surfacing semantic relationships between pages that pure structural checks
# miss.
# ────────────────────────────────────────────────────────────────────────


_RAW_LINK_RE = re.compile(r"\[\[raw/(.+?)\]\]")


def _wikilink_to_raw_key(link: str) -> str | None:
    """Convert a [[raw/X]] wikilink to the key used in raw embedding index.

    raw embed index keys are POSIX-relative paths from raw/, e.g. "RLHF.md".

    [[raw/RLHF]]            -> "RLHF.md"
    [[raw/articles/foo]]    -> "articles/foo.md"
    [[raw/paper.docx.md]]   -> "paper.docx.md"  (compound — keep as-is)
    """
    m = _RAW_LINK_RE.fullmatch(link.strip())
    if not m:
        return None
    inner = m.group(1)
    return inner if inner.endswith(".md") else inner + ".md"


def _mean_std(values: list[float]) -> tuple[float, float]:
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    import math
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    return mean, math.sqrt(var)


# Sanity floors for embedding checks. Without these, when similarities are
# all clustered around zero (sparse/orthogonal vectors, or empty corpus),
# the adaptive percentile threshold collapses to 0 and the check fires on
# every pair. The floor keeps "absolutely too dissimilar" pairs out
# regardless of distribution shape.
_MIN_SIMILARITY_FLOOR = 0.6     # cosine below this is never "similar"
_MIN_DRIFT_FLOOR = 0.1          # drift below this is never an outlier


def check_similar_but_unlinked(
    pages: list[Page],
    wiki_idx: Any,
    threshold_percentile: float = 95.0,
    min_similarity: float = _MIN_SIMILARITY_FLOOR,
) -> Iterable[Issue]:
    """Pairs of pages with high cosine similarity but no wikilink between them.

    Surfaces missing connections in the knowledge graph. Threshold is
    adaptive: top X% of all pairwise similarities. A floor (min_similarity)
    prevents false positives when the corpus has near-zero similarity
    everywhere (the adaptive threshold would collapse to 0 otherwise).

    Skips pairs where the embedding exists but the page no longer does
    (stale index — should be re-run after `bin/embed.py update`).
    """
    # Local import to avoid hard dependency when --approx isn't used
    from embed import cosine, percentile

    if not wiki_idx.items:
        return

    sims = wiki_idx.all_pairwise_similarities()
    if not sims:
        return

    threshold = max(percentile(sims, threshold_percentile), min_similarity)

    # Exclude meta pages and wiki root files (infrastructure, not content).
    # Same rule as check_orphan — these aren't part of the knowledge graph.
    def _is_content(p: Page) -> bool:
        return p.page_type != "meta" and p.folder != "" and p.folder != "meta"

    by_name = {p.name: p for p in pages if _is_content(p)}

    # Build link graph (existing wikilinks in body + frontmatter related/domain)
    outbound, _ = _build_link_graph(pages)

    names = list(wiki_idx.items.keys())
    seen: set[tuple[str, str]] = set()
    for i in range(len(names)):
        a = names[i]
        if a not in by_name:
            continue
        for j in range(i + 1, len(names)):
            b = names[j]
            if b not in by_name:
                continue
            sim = cosine(wiki_idx.items[a].vec, wiki_idx.items[b].vec)
            if sim <= threshold:
                continue
            # Already linked in either direction → not a missing link
            if b in outbound.get(a, set()) or a in outbound.get(b, set()):
                continue
            key = (a, b) if a < b else (b, a)
            if key in seen:
                continue
            seen.add(key)
            yield Issue("similar-but-unlinked", {
                "page_a": by_name[a].relpath(),
                "page_b": by_name[b].relpath(),
                "similarity": round(sim, 3),
                "threshold": round(threshold, 3),
            })


def check_synthesis_drift(
    pages: list[Page],
    wiki_idx: Any,
    raw_idx: Any,
    std_multiplier: float = 1.5,
    min_drift: float = _MIN_DRIFT_FLOOR,
) -> Iterable[Issue]:
    """Wiki pages whose embedding has drifted from the centroid of their sources.

    Detects synthesis that wandered too far from the source material —
    a heuristic for hallucination or excessive interpretation during ingest.

    Threshold is adaptive: mean drift + std_multiplier × std deviation.
    A floor (min_drift) keeps zero-variance distributions from triggering
    false positives — when all syntheses are equally tight, nothing should
    fire. Returns early if std == 0 (no outliers to find).
    """
    from embed import cosine, vec_mean

    if not wiki_idx.items or not raw_idx.items:
        return

    drifts: list[float] = []
    candidates: list[tuple[Page, float]] = []

    for p in pages:
        if p.fm is None:
            continue
        sources = p.fm.fields.get("sources")
        if not isinstance(sources, list) or not sources:
            continue
        keys: list[str] = []
        for src in sources:
            if not isinstance(src, str):
                continue
            k = _wikilink_to_raw_key(src)
            if k is not None:
                keys.append(k)
        source_vecs = [v for v in (raw_idx.get(k) for k in keys) if v is not None]
        if not source_vecs:
            continue
        page_vec = wiki_idx.get(p.name)
        if page_vec is None:
            continue
        centroid = vec_mean(source_vecs)
        drift = 1.0 - cosine(page_vec, centroid)
        drifts.append(drift)
        candidates.append((p, drift))

    if not drifts:
        return

    mean, std = _mean_std(drifts)
    if std == 0:
        return  # no variance → no statistical outliers
    threshold = max(mean + std_multiplier * std, min_drift)

    for p, drift in candidates:
        if drift <= threshold:
            continue
        yield Issue("synthesis-drift", {
            "where": p.relpath(),
            "drift": round(drift, 3),
            "threshold": round(threshold, 3),
        })


def compute_contradiction_candidates(
    pages: list[Page],
    wiki_idx: Any,
    threshold_percentile: float = 75.0,
    min_similarity: float = 0.5,
) -> list[dict[str, Any]]:
    """Top-similarity page pairs as candidates for Layer 2 contradiction check.

    The LLM-driven `contradiction` check in the lint skill (Layer 2) would
    otherwise need to inspect all O(n²) pairs of wiki pages — for 55 pages
    that's 1485 pairs. Pre-filtering by embedding similarity narrows the
    work to pairs that are at least topically related.

    Threshold:
    - Adaptive: top X% of pairwise similarities (default 75th percentile —
      i.e. top 25% of pairs).
    - Floor: 0.5 — slightly looser than similar-but-unlinked (0.6) since
      contradictions can exist between pages that are similar-on-topic but
      not nearly identical.

    Excludes meta pages (cache/log/summary/dashboards) — same rule as
    check_similar_but_unlinked.

    Returns list of {"page_a", "page_b", "similarity"} dicts sorted by
    similarity descending. Layer 2 reads them from lint-state.json.
    """
    from embed import cosine, percentile

    if not wiki_idx.items:
        return []

    sims = wiki_idx.all_pairwise_similarities()
    if not sims:
        return []

    threshold = max(percentile(sims, threshold_percentile), min_similarity)

    def _is_content(p: Page) -> bool:
        return p.page_type != "meta" and p.folder != "" and p.folder != "meta"

    by_name = {p.name: p for p in pages if _is_content(p)}

    names = list(wiki_idx.items.keys())
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for i in range(len(names)):
        a = names[i]
        if a not in by_name:
            continue
        for j in range(i + 1, len(names)):
            b = names[j]
            if b not in by_name:
                continue
            sim = cosine(wiki_idx.items[a].vec, wiki_idx.items[b].vec)
            if sim <= threshold:
                continue
            key = (a, b) if a < b else (b, a)
            if key in seen:
                continue
            seen.add(key)
            candidates.append({
                "page_a": by_name[a].relpath(),
                "page_b": by_name[b].relpath(),
                "similarity": round(sim, 3),
            })

    candidates.sort(key=lambda c: c["similarity"], reverse=True)
    return candidates


def run_all_checks(
    pages: list[Page],
    filter_type: str | None = None,
    extra_checks: list[tuple[str, Any]] | None = None,
) -> list[Issue]:
    """Run every registered check. If filter_type is set, run only that one.

    extra_checks are appended after the core registry — used by --approx
    to inject embedding-based checks that need extra arguments (bound via
    closures).
    """
    issues: list[Issue] = []
    all_checks = _CHECKS + (extra_checks or [])
    for type_name, check_fn in all_checks:
        if filter_type and type_name != filter_type:
            continue
        issues.extend(check_fn(pages))
    return issues


def _load_embedding_indexes() -> tuple[Any, Any] | None:
    """Load wiki and raw embedding indexes. Returns None if wiki is empty
    (graceful degradation signal)."""
    from embed import EmbedIndex, RAW_EMBED_PATH, WIKI_EMBED_PATH

    wiki_idx = EmbedIndex(WIKI_EMBED_PATH)
    wiki_idx.load()
    raw_idx = EmbedIndex(RAW_EMBED_PATH)
    raw_idx.load()

    if not wiki_idx.items:
        print(
            f"warning: --approx requested but {WIKI_EMBED_PATH} is empty",
            file=sys.stderr,
        )
        print("  hint: run 'python3 bin/embed.py update' first", file=sys.stderr)
        return None
    return wiki_idx, raw_idx


def _make_approx_checks(
    wiki_idx: Any,
    raw_idx: Any,
    threshold_percentile: float,
    std_multiplier: float,
) -> list[tuple[str, Any]]:
    """Bind loaded indexes into closures matching the (pages) -> Iterable[Issue]
    signature expected by the check registry."""

    def _similar(pages: list[Page]) -> Iterable[Issue]:
        return check_similar_but_unlinked(pages, wiki_idx, threshold_percentile)

    def _drift(pages: list[Page]) -> Iterable[Issue]:
        return check_synthesis_drift(pages, wiki_idx, raw_idx, std_multiplier)

    return [
        ("similar-but-unlinked", _similar),
        ("synthesis-drift", _drift),
    ]


# ────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="bypass aggregate-hash skip-check")
    ap.add_argument("--json", action="store_true", help="print full open_issues JSON to stdout")
    ap.add_argument("--check", type=str, default=None, help="run only one check by type")
    ap.add_argument(
        "--approx",
        action="store_true",
        help="enable embedding-based checks (similar-but-unlinked, synthesis-drift). "
             "Requires bin/embed.py update to have been run.",
    )
    ap.add_argument(
        "--similarity-percentile",
        type=float,
        default=95.0,
        help="for similar-but-unlinked: pairs above this percentile of pairwise similarities (default: 95)",
    )
    ap.add_argument(
        "--drift-std",
        type=float,
        default=1.5,
        help="for synthesis-drift: std multiplier above mean drift (default: 1.5)",
    )
    ap.add_argument(
        "--candidate-percentile",
        type=float,
        default=75.0,
        help="for Layer 2 contradiction pre-filter: pairs above this percentile "
             "of pairwise similarities go into contradiction_candidates (default: 75)",
    )
    args = ap.parse_args()

    pages = discover_pages()
    if not pages:
        print("no wiki pages found", file=sys.stderr)
        return 0

    wiki_hash = compute_wiki_hash(pages)
    state = load_lint_state()

    # Skip-check
    if not args.force:
        if state.get("wiki_hash") == wiki_hash and not state.get("open_issues"):
            print(f"wiki unchanged since last audit ({state.get('last_audit')}). clean. skipping.")
            return 0

    extra_checks: list[tuple[str, Any]] = []
    contradiction_candidates: list[dict[str, Any]] = []
    if args.approx:
        try:
            indexes = _load_embedding_indexes()
        except ImportError as e:
            print(f"warning: --approx unavailable ({e})", file=sys.stderr)
            indexes = None
        if indexes is not None:
            wiki_idx, raw_idx = indexes
            extra_checks = _make_approx_checks(
                wiki_idx, raw_idx,
                args.similarity_percentile, args.drift_std,
            )
            contradiction_candidates = compute_contradiction_candidates(
                pages, wiki_idx,
                threshold_percentile=args.candidate_percentile,
            )

    issues = run_all_checks(pages, filter_type=args.check, extra_checks=extra_checks)

    new_state: dict[str, Any] = {
        "wiki_hash": wiki_hash,
        "last_audit": dt.datetime.now().isoformat(timespec="seconds"),
        "files_checked": len(pages),
        "open_issues": [iss.to_dict() for iss in issues],
    }
    if contradiction_candidates:
        new_state["contradiction_candidates"] = contradiction_candidates
    save_lint_state(new_state)

    print(f"checked {len(pages)} pages, found {len(issues)} open issues")
    if args.json:
        print(json.dumps(new_state, ensure_ascii=False, indent=2))

    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
