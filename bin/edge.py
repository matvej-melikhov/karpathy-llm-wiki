#!/usr/bin/env python3
"""edge.py — boundary-score for the wiki.

Reads `wiki/**/*.md`, builds a wikilink graph and emits per-page boundary
scores to stdout (text or JSON). High score = page reaches into many
concepts but few pages link back to it: an integration frontier.

    boundary_score(p) = (out_degree(p) - in_degree(p)) * recency_weight(p)
    recency_weight(p) = exp(-days_since_updated(p) / HALFLIFE_DAYS)

Edges count both body wikilinks and frontmatter wikilinks (`related`,
`domain`). Targets that don't resolve to an existing scoreable page are
ignored (red links don't inflate out-degree).

Filtering:
- `type: meta` and `type: domain` pages are excluded from ranking.
  domain pages are MOCs (high out-degree by construction) and would
  always dominate the frontier; meta pages are infrastructure.
- Root meta files (index.md, log.md, cache.md, summary.md) excluded.
- `wiki/meta/`, `lint-reports/`, `kn-maps/` paths excluded.

Read-only. Doesn't write any state files.

Usage:
    python3 bin/edge.py                       # top-10 frontier, text
    python3 bin/edge.py --top N               # top N frontier
    python3 bin/edge.py --json                # JSON output
    python3 bin/edge.py --page PATH           # score one page (by stem or path)
    python3 bin/edge.py --include-zero        # include score <= 0

Exit codes:
    0  success
    2  usage error
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


# ────────────────────────────────────────────────────────────────────────
# Paths and constants
# ────────────────────────────────────────────────────────────────────────

WIKI_ROOT = Path("wiki")

HALFLIFE_DAYS = 30.0
DEFAULT_TOP = 10

EXCLUDE_TYPES = {"meta", "domain"}
EXCLUDE_FILENAMES = {"index.md", "log.md", "cache.md", "summary.md"}
EXCLUDE_PARENT_DIRS = {"meta", "lint-reports", "kn-maps"}

EXIT_OK = 0
EXIT_USAGE = 2


# ────────────────────────────────────────────────────────────────────────
# Parsing
# ────────────────────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_WIKILINK_RE = re.compile(r"(?<!\!)\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")


def _strip_quotes(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def _parse_yaml_subset(text: str) -> dict[str, Any]:
    """Parse the restricted YAML subset used in our frontmatter:
    top-level keys with string or list values. Lists either block-style
    (newline + `  - item`) or inline `[a, b]`. Quoted strings supported.
    """
    fields: dict[str, Any] = {}
    lines = text.split("\n")
    i = 0
    current_key: str | None = None
    current_list: list[str] | None = None

    while i < len(lines):
        line = lines[i]
        stripped = line.rstrip()
        if not stripped or stripped.lstrip().startswith("#"):
            i += 1
            continue

        list_item_match = re.match(r"^\s+-\s*(.*)$", line)
        if list_item_match and current_list is not None:
            current_list.append(_strip_quotes(list_item_match.group(1).strip()))
            i += 1
            continue

        if current_key is not None and current_list is not None:
            fields[current_key] = current_list
            current_key = None
            current_list = None

        kv_match = re.match(r"^([a-z_][a-z_0-9]*)\s*:\s*(.*)$", line, re.IGNORECASE)
        if not kv_match:
            i += 1
            continue
        key = kv_match.group(1)
        value_str = kv_match.group(2).strip()

        if value_str == "":
            if i + 1 < len(lines) and re.match(r"^\s+-\s*", lines[i + 1]):
                current_key = key
                current_list = []
            else:
                fields[key] = None
            i += 1
            continue

        if value_str.startswith("[") and value_str.endswith("]"):
            inline = value_str[1:-1].strip()
            fields[key] = [] if not inline else [_strip_quotes(x.strip()) for x in inline.split(",")]
            i += 1
            continue

        fields[key] = _strip_quotes(value_str)
        i += 1

    if current_key is not None and current_list is not None:
        fields[current_key] = current_list
    return fields


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    return _parse_yaml_subset(m.group(1)), text[m.end():]


def _normalize_target(target: str) -> str:
    """Wikilink target → basename stem. raw/-prefixed targets are kept as-is
    (they are external sources, not wiki pages)."""
    if target.startswith("raw/"):
        return target
    if "/" in target:
        return target.rsplit("/", 1)[-1]
    return target


def extract_wikilinks(text: str) -> list[str]:
    """All wikilink targets in text, normalized to basename. Strips fenced
    and inline code so doc examples don't pollute the graph."""
    cleaned = text.replace(r"\|", "|")
    cleaned = re.sub(r"```.*?```", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"`[^`\n]*`", "", cleaned)
    return [_normalize_target(m.group(1).strip()) for m in _WIKILINK_RE.finditer(cleaned)]


# ────────────────────────────────────────────────────────────────────────
# Page model
# ────────────────────────────────────────────────────────────────────────


def _included(path: Path, fm: dict[str, Any]) -> bool:
    """Return True if a wiki page is scoreable."""
    if path.name in EXCLUDE_FILENAMES:
        return False
    for parent in path.parents:
        if parent.name in EXCLUDE_PARENT_DIRS:
            return False
    if fm.get("type") in EXCLUDE_TYPES:
        return False
    return True


def collect_pages() -> dict[str, dict[str, Any]]:
    """Walk wiki/ and return {stem: {path, fm, body, fm_links}}.

    `stem` is the filename without .md — Obsidian wikilinks resolve by stem.
    `fm_links` are wikilinks pulled from frontmatter list fields
    (`related`, `domain`); they augment body links for graph construction.
    """
    pages: dict[str, dict[str, Any]] = {}
    if not WIKI_ROOT.is_dir():
        return pages

    for md in sorted(WIKI_ROOT.rglob("*.md")):
        try:
            text = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm, body = parse_frontmatter(text)
        if not _included(md, fm):
            continue

        fm_links: list[str] = []
        for fld in ("related", "domain"):
            value = fm.get(fld)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        for t in extract_wikilinks(item):
                            if t.startswith("raw/"):
                                continue
                            fm_links.append(t)

        pages[md.stem] = {
            "path": md.as_posix(),
            "fm": fm,
            "body": body,
            "fm_links": fm_links,
        }
    return pages


# ────────────────────────────────────────────────────────────────────────
# Graph and scoring
# ────────────────────────────────────────────────────────────────────────


def build_graph(pages: dict[str, dict[str, Any]]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Return (outbound, inbound) where each maps stem -> set(stem). Only
    edges whose target is itself a scoreable page are counted. Self-loops
    are ignored.
    """
    out_edges: dict[str, set[str]] = {k: set() for k in pages}
    in_edges: dict[str, set[str]] = {k: set() for k in pages}
    for src, entry in pages.items():
        targets: set[str] = set()
        targets.update(extract_wikilinks(entry["body"]))
        targets.update(entry["fm_links"])
        for t in targets:
            if t == src or t.startswith("raw/"):
                continue
            if t in pages:
                out_edges[src].add(t)
                in_edges[t].add(src)
    return out_edges, in_edges


def _days_since(date_str: str | None) -> float:
    """Days since YYYY-MM-DD; large sentinel for missing/malformed dates."""
    if not date_str:
        return 10_000.0
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        return 10_000.0
    return max(0.0, float((date.today() - d).days))


def _recency_weight(days: float, halflife: float = HALFLIFE_DAYS) -> float:
    return math.exp(-days / halflife)


def score_page(stem: str,
               pages: dict[str, dict[str, Any]],
               out_edges: dict[str, set[str]],
               in_edges: dict[str, set[str]]) -> dict[str, Any]:
    entry = pages[stem]
    fm = entry["fm"]
    out_deg = len(out_edges.get(stem, set()))
    in_deg = len(in_edges.get(stem, set()))
    days = _days_since(fm.get("updated") or fm.get("created"))
    rw = _recency_weight(days)
    score = (out_deg - in_deg) * rw

    domain_field = fm.get("domain")
    domains: list[str] = []
    if isinstance(domain_field, list):
        domains = [str(d) for d in domain_field if isinstance(d, str)]

    return {
        "name": stem,
        "path": entry["path"],
        "type": fm.get("type") or "",
        "domain": domains,
        "out_degree": out_deg,
        "in_degree": in_deg,
        "out_targets": sorted(out_edges.get(stem, set())),
        "age_days": days,
        "recency_weight": round(rw, 4),
        "score": round(score, 4),
    }


# ────────────────────────────────────────────────────────────────────────
# Output
# ────────────────────────────────────────────────────────────────────────


def _print_text(scored: list[dict[str, Any]], total_pages: int) -> None:
    print("# Edge Score Report")
    print(f"scoreable pages: {total_pages}; halflife: {int(HALFLIFE_DAYS)} days")
    print(f"excluded types: {', '.join(sorted(EXCLUDE_TYPES))}")
    if not scored:
        print("\nNo positive-score frontier pages found.")
        return
    print("")
    print("| # | score | out | in | age_d | type | name |")
    print("|---|---|---|---|---|---|---|")
    for i, s in enumerate(scored, 1):
        print(
            f"| {i} | {s['score']:.3f} | {s['out_degree']} | {s['in_degree']} | "
            f"{int(s['age_days'])} | {s['type']} | {s['name']} |"
        )


def run(top: int, want_json: bool, include_zero: bool, page_filter: str | None) -> int:
    pages = collect_pages()
    out_edges, in_edges = build_graph(pages)
    scored = [score_page(k, pages, out_edges, in_edges) for k in pages]

    if page_filter:
        key = Path(page_filter).stem
        matched = [s for s in scored if s["name"] == key or s["path"] == page_filter]
        if not matched:
            print(f"ERR: no scoreable page matches '{page_filter}'", file=sys.stderr)
            return EXIT_USAGE
        scored = matched
    else:
        if not include_zero:
            scored = [s for s in scored if s["score"] > 0.0]
        scored.sort(key=lambda s: (-s["score"], s["name"]))
        scored = scored[:top]

    if want_json:
        print(json.dumps({
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "halflife_days": HALFLIFE_DAYS,
            "page_count_scoreable": len(pages),
            "excluded_types": sorted(EXCLUDE_TYPES),
            "results": scored,
        }, indent=2, ensure_ascii=False))
    else:
        _print_text(scored, len(pages))
    return EXIT_OK


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Boundary-score for the wiki frontier.")
    p.add_argument("--top", type=int, default=DEFAULT_TOP, help="show top N (default 10)")
    p.add_argument("--json", action="store_true", help="emit JSON instead of text")
    p.add_argument("--include-zero", action="store_true", help="include pages with score <= 0")
    p.add_argument("--page", default=None, help="score one page (by stem or path)")
    args = p.parse_args(argv)
    if args.top < 1:
        print("ERR: --top must be >= 1", file=sys.stderr)
        return EXIT_USAGE
    return run(args.top, args.json, args.include_zero, args.page)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
