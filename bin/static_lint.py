#!/usr/bin/env python3
"""Static lint for the wiki — Layer 1.

Implements deterministic checks that don't need LLM judgment. Outputs a
structured `wiki/meta/lint-state.json` with `open_issues`. Semantic checks
(contradiction, outdated-claim, missing-concept, tag-casing) stay agent-
driven — they're done in a separate LLM pass via the `lint` skill.

This script is the "first layer" of two-layer lint:
1. bin/static_lint.py — fast, deterministic, schema/structural checks
2. lint skill — LLM pass for semantic checks; reads lint-state.json,
   adds its own `open_issues` entries.

Usage:
    python3 bin/static_lint.py            # --quick (default): skip-check on
                                            wiki_hash; new issues only for
                                            pages whose content hash changed
                                            since last lint; old issues for
                                            non-touched pages preserved
    python3 bin/static_lint.py --full     # full audit: ignore skip-check;
                                            re-emit all issues; always run
                                            on all pages
    python3 bin/static_lint.py --json     # also print full open_issues JSON
    python3 bin/static_lint.py --check <type>   # run only one check (debug)

Embedding-based checks (similar-but-unlinked, synthesis-drift,
contradiction_candidates) run in both modes when wiki/meta/embeddings.json
is available. If absent — warning + skip those checks only.

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
LINT_STATE_PATH = WIKI_ROOT / "meta" / "lint-reports" / "lint-state.json"

RAW_ROOT = Path("raw")
RAW_FORMATS_DIR = RAW_ROOT / "formats"

TEMPLATES_DIR = Path("_templates")

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
    "minds": "mind",
}
CONTENT_FOLDERS = list(FOLDER_TO_TYPE.keys())

DEFAULT_VALID_STATUSES = {"evaluation", "in-progress", "ready"}
MIND_VALID_STATUSES = {"draft", "stable", "deprecated"}
# Backward-compat alias for the original constant name.
VALID_STATUSES = DEFAULT_VALID_STATUSES


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
        # Skip auto-generated meta artifacts. Supports both layouts:
        # - subdir: wiki/meta/lint-reports/, wiki/meta/kn-maps/
        # - legacy flat: wiki/meta/lint-report-*.md, knowledge-map-*.md
        if md.parent.name in ("lint-reports", "kn-maps"):
            continue
        if md.parent.name == "meta" and (
            md.name.startswith("lint-report-")
            or md.name.startswith("knowledge-map-")
        ):
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


def compute_page_hashes(pages: list[Page]) -> dict[str, str]:
    """Per-page sha256 over full content (frontmatter + body). Used in --quick
    mode to detect which pages changed since last lint run.
    """
    return {
        p.relpath(): hashlib.sha256(p.text.encode("utf-8")).hexdigest()
        for p in pages
    }


def compute_touched_pages(
    current_hashes: dict[str, str],
    stored_hashes: dict[str, str],
) -> set[str]:
    """Pages whose content hash changed since last lint, plus newly created
    pages (in current but not in stored). Used to scope --quick mode.

    Bootstrap (stored_hashes empty) returns ALL current pages — first run
    treats everything as touched, equivalent to a full audit.
    """
    if not stored_hashes:
        return set(current_hashes.keys())
    touched: set[str] = set()
    for path, current in current_hashes.items():
        stored = stored_hashes.get(path)
        if stored is None or stored != current:
            touched.add(path)
    return touched


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
    """status: must match the enum for the page's type.

    - mind: draft / stable / deprecated (default fix: draft)
    - other content types: evaluation / in-progress / ready (default fix: in-progress)
    - entity: skipped — `status` doesn't belong there at all per template,
      and `check_invalid_fields` will flag it as an extra field
      (correct fix: remove, not change-to-valid-value).
    """
    for p in pages:
        if p.fm is None:
            continue
        if p.page_type == "entity":
            continue
        status = p.fm.fields.get("status")
        if status is None:
            continue
        if p.page_type == "mind":
            valid = MIND_VALID_STATUSES
            default_fix = "draft"
        else:
            valid = DEFAULT_VALID_STATUSES
            default_fix = "in-progress"
        if status not in valid:
            yield Issue("status-not-in-enum", {
                "where": p.relpath(),
                "value": status,
                "fix": default_fix,
            })


def _parse_raw_yaml_entries(fm_text: str) -> dict[str, str]:
    """Parse frontmatter into {field_name: raw full entry text}.

    Raw entry preserves the exact form from source (single-line `k: v` or
    multi-line block list). Used both for detection (fields = .keys()) and
    for missing-field auto-fix (paste raw entry into target page).
    """
    entries: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []
    for line in fm_text.split("\n"):
        # Top-level key: starts at column 0 with letter/underscore and has ':'
        if line and line[0] not in " \t-#" and ":" in line:
            if current_key is not None:
                entries[current_key] = "\n".join(current_lines).rstrip()
            key, _, _ = line.partition(":")
            current_key = key.strip()
            current_lines = [line]
        else:
            if current_key is not None:
                current_lines.append(line)
    if current_key is not None:
        entries[current_key] = "\n".join(current_lines).rstrip()
    return entries


def _load_template_schemas() -> dict[str, dict[str, str]]:
    """Read `_templates/<type>.md`, return type → {field: raw full entry}.

    Source of truth for `check_invalid_fields` (detection uses keys) and
    for missing-field auto-fix (uses the raw entry as default).

    Returns empty dict if templates directory is missing (degrade
    gracefully — invalid-fields just won't fire).
    """
    schemas: dict[str, dict[str, str]] = {}
    if not TEMPLATES_DIR.is_dir():
        return schemas
    for tmpl in sorted(TEMPLATES_DIR.glob("*.md")):
        try:
            text = tmpl.read_text(encoding="utf-8")
        except OSError:
            continue
        m = _FRONTMATTER_RE.match(text)
        if not m:
            continue
        schemas[tmpl.stem] = _parse_raw_yaml_entries(m.group(1))
    return schemas


def check_invalid_fields(pages: list[Page]) -> Iterable[Issue]:
    """Frontmatter has fields not in template (extra) or missing fields
    from template. Source of truth — `_templates/<type>.md`.

    Emits two subtypes:
    - `subtype: "extra"` — field is in page frontmatter but not in template
    - `subtype: "missing"` — field is in template but not in page

    Skips:
    - meta pages (no template, structure is operation-driven)
    - pages with unknown `type:` (no matching template)
    - `summary` for `subtype: "missing"` — covered by `check_missing_summary`
      with agent-fix that generates content (vs script-fix here which would
      just add empty placeholder)
    """
    schemas = _load_template_schemas()
    for p in pages:
        if p.fm is None:
            continue
        ptype = p.page_type
        if ptype is None or ptype == "meta":
            continue
        type_schema = schemas.get(ptype)
        if type_schema is None:
            continue  # no template for this type
        expected = set(type_schema.keys())
        actual = set(p.fm.fields.keys())

        for extra in sorted(actual - expected):
            yield Issue("invalid-fields", {
                "where": p.relpath(),
                "subtype": "extra",
                "field": extra,
            })

        for missing in sorted(expected - actual):
            if missing == "summary":
                continue  # has its own check (check_missing_summary)
            yield Issue("invalid-fields", {
                "where": p.relpath(),
                "subtype": "missing",
                "field": missing,
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


def _normalize_wiki_target(target: str) -> str:
    """Strip path prefix from wiki wikilink target → basename.

    Per schema: filenames are unique by basename across the entire vault,
    so wikilinks should use just the stem (`[[RLHF]]`, not `[[wiki/ideas/RLHF]]`).
    Obsidian renders both forms identically, but consistency matters for
    lint and for our embedding-summary lookup.

    Special case: `raw/...` references DO use path structure (raw/articles/foo,
    raw/formats/...) and must not be normalized — they're not wiki pages.

    Examples:
        [[RLHF]]              → "RLHF"
        [[wiki/ideas/RLHF]]   → "RLHF"
        [[ideas/RLHF]]        → "RLHF"
        [[raw/articles/foo]]  → "raw/articles/foo"  (unchanged)
    """
    if target.startswith("raw/"):
        return target
    if "/" in target:
        return target.rsplit("/", 1)[-1]
    return target


def _extract_wikilinks(text: str) -> list[str]:
    """Return basename targets of all wikilinks in text.

    Path-prefixed wiki wikilinks (e.g., [[wiki/ideas/RLHF]]) are normalized
    to basename ("RLHF"). raw/-prefixed targets are preserved as-is.
    """
    cleaned = _normalize_wikilink_text(text)
    return [
        _normalize_wiki_target(m.group(1).strip())
        for m in _WIKILINK_RE.finditer(cleaned)
    ]


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
                target = _normalize_wiki_target(m.group(1).strip())
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
    """Page with zero inbound wikilinks. Excludes:
    - meta pages (infrastructure, not part of the knowledge graph)
    - wiki root files (index/log/cache/summary)
    - pages with status: deprecated (intentionally retired, replaced by a
      newer revision; not expected to be linked)
    """
    _, inbound = _build_link_graph(pages)
    for p in pages:
        if p.page_type == "meta":
            continue
        # also skip wiki root files even if they're not type:meta
        if p.folder == "":
            continue
        if p.fm is not None and p.fm.fields.get("status") == "deprecated":
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
                t = _normalize_wiki_target(m.group(1).strip())
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


def check_missing_summary(pages: list[Page]) -> Iterable[Issue]:
    """Content page (idea/entity/domain/question) without non-empty `summary:`
    in frontmatter. Required because `wiki/index.md` is auto-generated from
    this field — page without summary appears in index with placeholder text.
    """
    for p in pages:
        if p.fm is None:
            continue
        if p.folder not in CONTENT_FOLDERS:
            continue
        summary = p.fm.fields.get("summary")
        if isinstance(summary, str) and summary.strip():
            continue
        yield Issue("missing-summary", {
            "where": p.relpath(),
            "page_type": p.page_type or FOLDER_TO_TYPE.get(p.folder, ""),
        })


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
                target = _normalize_wiki_target(m.group(1).strip())
                if target.startswith("raw/"):
                    continue
                if target not in domain_pages:
                    yield Issue("dangling-domain-ref", {
                        "where": p.relpath(),
                        "missing_domain": target,
                    })


def _build_canonical_fix(original: str, normalized_target: str) -> str:
    """Build a fix string for non-canonical wikilink that preserves #anchor
    and |alias parts.

    [[wiki/ideas/RLHF]]                       → [[RLHF]]
    [[wiki/ideas/RLHF#Section]]               → [[RLHF#Section]]
    [[wiki/ideas/RLHF|Alias]]                 → [[RLHF|Alias]]
    [[wiki/ideas/RLHF#Section|Alias]]         → [[RLHF#Section|Alias]]
    """
    inner = original[2:-2]  # strip [[ and ]]
    alias_part = ""
    if "|" in inner:
        inner, alias = inner.split("|", 1)
        alias_part = "|" + alias
    anchor_part = ""
    if "#" in inner:
        inner, anchor = inner.split("#", 1)
        anchor_part = "#" + anchor
    return "[[" + normalized_target + anchor_part + alias_part + "]]"


def check_non_canonical_wikilink(pages: list[Page]) -> Iterable[Issue]:
    """Wikilink uses path-prefixed form (e.g., [[wiki/ideas/RLHF]]) where the
    canonical form is just the basename ([[RLHF]]).

    Per schema, filenames are unique by basename across the entire vault, so
    `[[Page]]` resolves correctly without a path. Path-prefixed wikilinks
    work in Obsidian but break our lint and embedding pipeline (which key by
    basename) — so we normalize and flag for auto-fix.

    raw/ references are SKIPPED — those legitimately use path structure
    (raw/articles/foo, raw/formats/...) and aren't normalized.

    Auto-fix: replace `[[X/Y/Page]]` with `[[Page]]` (preserving alias and
    anchor parts via the `fix` payload).

    Sources scanned:
    - Page bodies (excluding fenced code and inline code)
    - frontmatter `related:` and `domain:` fields

    `wiki/index.md` is NOT scanned — оно генерируется `bin/gen_index.py` из
    canonical wikilinks по построению.

    Skips meta pages (their content is operations log, not knowledge graph).
    """
    seen: set[tuple[str, str]] = set()

    for p in pages:
        if p.page_type == "meta" or p.folder == "meta":
            continue
        if p.fm is None and not p.body:
            continue

        # Body (with code blocks stripped)
        if p.body:
            in_code = False
            for line_no, raw_line in enumerate(p.body.split("\n"), start=1):
                stripped = raw_line.lstrip()
                if stripped.startswith("```"):
                    in_code = not in_code
                    continue
                if in_code:
                    continue
                line = re.sub(r"`[^`\n]*`", "", raw_line).replace(r"\|", "|")
                for m in _WIKILINK_RE.finditer(line):
                    raw_target = m.group(1).strip()
                    if raw_target.startswith("raw/"):
                        continue
                    if "/" not in raw_target:
                        continue
                    fix = _build_canonical_fix(m.group(0), _normalize_wiki_target(raw_target))
                    key = (p.relpath(), m.group(0))
                    if key in seen:
                        continue
                    seen.add(key)
                    yield Issue("non-canonical-wikilink", {
                        "where": p.relpath(),
                        "link": m.group(0),
                        "fix": fix,
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
                for m in _WIKILINK_RE.finditer(v):
                    raw_target = m.group(1).strip()
                    if raw_target.startswith("raw/"):
                        continue
                    if "/" not in raw_target:
                        continue
                    fix = _build_canonical_fix(m.group(0), _normalize_wiki_target(raw_target))
                    key = (p.relpath(), m.group(0) + ":" + fld)
                    if key in seen:
                        continue
                    seen.add(key)
                    yield Issue("non-canonical-wikilink", {
                        "where": p.relpath(),
                        "link": m.group(0),
                        "fix": fix,
                        "context": f"frontmatter {fld}",
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


# ────────────────────────────────────────────────────────────────────────
# Auto-fix functions
# ────────────────────────────────────────────────────────────────────────
#
# For each script-fixable issue type, a pure text mutator
# `(content, payload) -> new_content`. The I/O wrapper `_apply_text_fix`
# reads the file, calls the mutator, writes if changed.
#
# `binary-source-outside-formats` is special: it's an inter-file move
# (uses `bin/rename_wiki_page.py` subprocess), not a within-file mutation.
#
# Issue types NOT in FIX_HANDLERS stay in `open_issues` as-is — they
# need agent or user intervention (missing-summary, ask-issues, etc.).
# ────────────────────────────────────────────────────────────────────────


def _split_frontmatter(content: str) -> tuple[str | None, str]:
    """Return (fm_inner, body_with_delim_stripped). (None, content) if no FM.

    To recombine: `_join_frontmatter(fm_inner, body)`.
    """
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return None, content
    return m.group(1), content[m.end():]


def _join_frontmatter(fm_inner: str, body: str) -> str:
    return f"---\n{fm_inner}\n---\n{body}"


def _remove_yaml_field(fm: str, field: str) -> str:
    """Drop a top-level field (and its multi-line continuation) from raw FM."""
    lines = fm.split("\n")
    result: list[str] = []
    in_target = False
    for line in lines:
        is_top_level = bool(line) and line[0] not in " \t-#" and ":" in line
        if is_top_level:
            key = line.partition(":")[0].strip()
            in_target = (key == field)
            if in_target:
                continue
        if in_target:
            continue
        result.append(line)
    return "\n".join(result)


_TEMPLATER_DATE_RE = re.compile(r"<%\s*tp\.date\.now\([^)]*\)\s*%>")
_TEMPLATER_TITLE_RE = re.compile(r"<%\s*tp\.file\.title\s*%>")


def _resolve_templater(raw: str, page_title: str = "") -> str:
    """Replace Templater placeholders with concrete values."""
    today = dt.date.today().isoformat()
    raw = _TEMPLATER_DATE_RE.sub(today, raw)
    raw = _TEMPLATER_TITLE_RE.sub(page_title, raw)
    return raw


# ── Per-issue-type mutators ─────────────────────────────────────────────


def _fix_status_not_in_enum_in_text(content: str, payload: dict) -> str:
    fm, body = _split_frontmatter(content)
    if fm is None:
        return content
    new_fm = re.sub(
        r"^status:\s*.*$",
        f"status: {payload['fix']}",
        fm,
        count=1,
        flags=re.MULTILINE,
    )
    return _join_frontmatter(new_fm, body)


def _fix_folder_type_mismatch_in_text(content: str, payload: dict) -> str:
    fm, body = _split_frontmatter(content)
    if fm is None:
        return content
    new_fm = re.sub(
        r"^type:\s*.*$",
        f"type: {payload['expected_type']}",
        fm,
        count=1,
        flags=re.MULTILINE,
    )
    return _join_frontmatter(new_fm, body)


def _fix_inline_tags_in_text(content: str, payload: dict) -> str:
    fm, body = _split_frontmatter(content)
    if fm is None:
        return content
    m = re.search(r"^tags:\s*\[(.*)\]\s*$", fm, re.MULTILINE)
    if not m:
        return content
    raw_items = [x.strip() for x in m.group(1).split(",") if x.strip()]
    cleaned: list[str] = []
    for x in raw_items:
        if (x.startswith('"') and x.endswith('"')) or (x.startswith("'") and x.endswith("'")):
            cleaned.append(x[1:-1])
        else:
            cleaned.append(x)
    if not cleaned:
        replacement = "tags: []"
    else:
        replacement = "tags:\n" + "\n".join(f"  - {x}" for x in cleaned)
    new_fm = fm[: m.start()] + replacement + fm[m.end():]
    return _join_frontmatter(new_fm, body)


def _fix_non_canonical_wikilink_in_text(content: str, payload: dict) -> str:
    """Replace literal link text with canonical fix everywhere in file."""
    return content.replace(payload["link"], payload["fix"])


def _fix_raw_link_with_extension_in_text(content: str, payload: dict) -> str:
    """[[raw/X.md]] → [[raw/X]] (strip trailing .md before ]])."""
    link = payload["link"]
    fix = re.sub(r"\.md\]\]$", "]]", link)
    if fix == link:
        return content
    return content.replace(link, fix)


def _fix_raw_ref_in_body_in_text(content: str, payload: dict) -> str:
    """Remove the wikilink occurrence from body text. Frontmatter untouched."""
    fm, body = _split_frontmatter(content)
    if fm is None:
        return content
    link = payload["link"]
    # Replace ` link ` (with surrounding space) with single space, then any
    # remaining bare occurrence with empty. Keep it simple — most real cases
    # are inline mentions.
    new_body = body.replace(f" {link}", "").replace(link, "")
    if new_body == body:
        return content
    return _join_frontmatter(fm, new_body)


def _fix_invalid_fields_extra_in_text(content: str, payload: dict) -> str:
    fm, body = _split_frontmatter(content)
    if fm is None:
        return content
    new_fm = _remove_yaml_field(fm, payload["field"])
    return _join_frontmatter(new_fm, body)


def _fix_invalid_fields_missing_in_text(
    content: str,
    payload: dict,
    schemas: dict[str, dict[str, str]] | None = None,
    page_title: str = "",
) -> str:
    fm, body = _split_frontmatter(content)
    if fm is None:
        return content
    # Identify page type from frontmatter
    type_match = re.search(r"^type:\s*(.+?)\s*$", fm, re.MULTILINE)
    if not type_match:
        return content
    ptype = type_match.group(1).strip()
    if schemas is None:
        schemas = _load_template_schemas()
    type_schema = schemas.get(ptype)
    if not type_schema:
        return content
    raw_entry = type_schema.get(payload["field"])
    if raw_entry is None:
        return content
    resolved = _resolve_templater(raw_entry, page_title=page_title)
    new_fm = fm.rstrip("\n") + "\n" + resolved
    return _join_frontmatter(new_fm, body)


# ── I/O wrapper and dispatcher ─────────────────────────────────────────


def _apply_text_fix(payload: dict, mutator: Any) -> bool:
    """Read file at payload['where'], apply mutator, write if changed."""
    path = Path(payload["where"])
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False
    new_content = mutator(content, payload)
    if new_content == content:
        return False
    path.write_text(new_content, encoding="utf-8")
    return True


def _fix_invalid_fields(payload: dict) -> bool:
    """Subtype-aware dispatch for invalid-fields issues."""
    subtype = payload.get("subtype")
    if subtype == "extra":
        return _apply_text_fix(payload, _fix_invalid_fields_extra_in_text)
    if subtype == "missing":
        path = Path(payload["where"])
        page_title = path.stem
        schemas = _load_template_schemas()

        def mutator(content: str, p: dict) -> str:
            return _fix_invalid_fields_missing_in_text(
                content, p, schemas=schemas, page_title=page_title
            )

        return _apply_text_fix(payload, mutator)
    return False


def _fix_binary_source_outside_formats(payload: dict) -> bool:
    """Subprocess to bin/rename_wiki_page.py (handles wikilink updates + mv)."""
    import subprocess
    where = payload["where"]
    suggested = payload["suggested"]
    script = Path(__file__).resolve().parent / "rename_wiki_page.py"
    result = subprocess.run(
        ["python3", str(script), where, suggested],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


FIX_HANDLERS: dict[str, Any] = {
    "status-not-in-enum": lambda p: _apply_text_fix(p, _fix_status_not_in_enum_in_text),
    "invalid-fields": _fix_invalid_fields,
    "inline-tags": lambda p: _apply_text_fix(p, _fix_inline_tags_in_text),
    "non-canonical-wikilink": lambda p: _apply_text_fix(p, _fix_non_canonical_wikilink_in_text),
    "folder-type-mismatch": lambda p: _apply_text_fix(p, _fix_folder_type_mismatch_in_text),
    "raw-link-with-extension": lambda p: _apply_text_fix(p, _fix_raw_link_with_extension_in_text),
    "raw-ref-in-body": lambda p: _apply_text_fix(p, _fix_raw_ref_in_body_in_text),
    "binary-source-outside-formats": _fix_binary_source_outside_formats,
}


def issue_involves_pages(issue: Issue, page_set: set[str]) -> bool:
    """True if issue references at least one page in `page_set`. Used to
    filter --quick scope: keep only issues touching changed pages.

    Issue payload conventions: `where`/`page_a`/`page_b`/`mentioned_in`
    are the page-pointer fields across all checks.
    """
    payload = issue.payload
    candidates: list[str] = []
    for key in ("where", "page_a", "page_b"):
        v = payload.get(key)
        if isinstance(v, str):
            candidates.append(v)
    mentioned = payload.get("mentioned_in")
    if isinstance(mentioned, list):
        candidates.extend(p for p in mentioned if isinstance(p, str))
    return any(p in page_set for p in candidates)


def apply_auto_fixes(issues: list[Issue]) -> tuple[list[Issue], int]:
    """Try to apply auto-fix for each issue. Return (remaining, fixed_count).

    Issues without a handler stay in `remaining`. Failed fixes (handler
    returned False or raised) also stay — they'll be retried next /lint.
    """
    remaining: list[Issue] = []
    fixed = 0
    for issue in issues:
        handler = FIX_HANDLERS.get(issue.type)
        if handler is None:
            remaining.append(issue)
            continue
        try:
            if handler(issue.payload):
                fixed += 1
            else:
                remaining.append(issue)
        except Exception:
            remaining.append(issue)
    return remaining, fixed


# Registry: ordered list of (issue_type_string, check_function)
_CHECKS: list[tuple[str, Any]] = [
    ("status-not-in-enum", check_status_not_in_enum),
    ("invalid-fields", check_invalid_fields),
    ("inline-tags", check_inline_tags),
    ("non-canonical-wikilink", check_non_canonical_wikilink),
    ("folder-type-mismatch", check_folder_type_mismatch),
    ("raw-link-with-extension", check_raw_link_with_extension),
    ("raw-ref-in-body", check_raw_ref_in_body),
    ("dead-link", check_dead_link),
    ("orphan", check_orphan),
    ("asymmetric-related", check_asymmetric_related),
    ("dangling-domain-ref", check_dangling_domain_ref),
    ("missing-summary", check_missing_summary),
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
        # mind pages are author reflections, not source syntheses — drift is meaningless
        if p.page_type == "mind":
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
    ap.add_argument(
        "--full",
        action="store_true",
        help="full audit: ignore skip-check and per-page touched scope. "
             "Default mode is --quick: skip if wiki unchanged, scope new "
             "issues to pages whose content hash changed since last lint.",
    )
    ap.add_argument("--json", action="store_true", help="print full open_issues JSON to stdout")
    ap.add_argument("--check", type=str, default=None, help="run only one check by type (debug)")
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
    current_page_hashes = compute_page_hashes(pages)
    state = load_lint_state()
    stored_page_hashes = state.get("page_hashes", {})

    touched: set[str] = compute_touched_pages(current_page_hashes, stored_page_hashes)

    # Skip-check (only --quick mode). --full always re-audits.
    if not args.full:
        if state.get("wiki_hash") == wiki_hash and not state.get("open_issues"):
            print(f"wiki unchanged since last audit ({state.get('last_audit')}). clean. skipping.")
            return 0

    # Always-on embedding checks. If indexes unavailable — warning + skip.
    extra_checks: list[tuple[str, Any]] = []
    contradiction_candidates: list[dict[str, Any]] = []
    try:
        indexes = _load_embedding_indexes()
    except ImportError as e:
        print(f"warning: embedding checks unavailable ({e})", file=sys.stderr)
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

    # Apply script auto-fixes inline. Anything not script-fixable (agent fix
    # or ask-issue) stays in `remaining`.
    remaining, fixed = apply_auto_fixes(issues)

    if fixed:
        # Re-discover and re-hash because file contents changed
        pages = discover_pages()
        wiki_hash = compute_wiki_hash(pages)
        current_page_hashes = compute_page_hashes(pages)

    # Scope filtering: in --quick, keep new issues only for touched pages,
    # preserve old issues for non-touched pages (--full will re-validate).
    if args.full:
        merged = remaining
    else:
        old_issues_to_keep = []
        for raw in state.get("open_issues", []):
            old_issue = Issue(raw["type"], {k: v for k, v in raw.items() if k != "type"})
            if not issue_involves_pages(old_issue, touched):
                old_issues_to_keep.append(old_issue)
        new_issues_in_scope = [i for i in remaining if issue_involves_pages(i, touched)]
        merged = old_issues_to_keep + new_issues_in_scope

    new_state: dict[str, Any] = {
        "wiki_hash": wiki_hash,
        "last_audit": dt.datetime.now().isoformat(timespec="seconds"),
        "files_checked": len(pages),
        "page_hashes": current_page_hashes,
        "open_issues": [iss.to_dict() for iss in merged],
    }
    if contradiction_candidates:
        new_state["contradiction_candidates"] = contradiction_candidates
    save_lint_state(new_state)

    mode = "full" if args.full else "quick"
    print(
        f"checked {len(pages)} pages [{mode}], "
        f"touched {len(touched)}, fixed {fixed}, "
        f"{len(merged)} remaining"
    )
    if args.json:
        print(json.dumps(new_state, ensure_ascii=False, indent=2))

    return 1 if merged else 0


if __name__ == "__main__":
    sys.exit(main())
