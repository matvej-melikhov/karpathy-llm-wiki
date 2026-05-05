#!/usr/bin/env python3
"""Knowledge map: UMAP-projected wiki visualization with statistics.

Generates three artifacts on each run:

    _attachments/knowledge-map-YYYY-MM-DD.html       interactive Plotly viz
    _attachments/knowledge-map-YYYY-MM-DD.png        static export
    wiki/meta/kn-maps/knowledge-map-YYYY-MM-DD.md    markdown page with stats

The .md page is versioned (timestamp in filename) so successive runs form
a history of wiki growth, like lint-report-*.md.

Visualization:
- Each content page is one point in 2D UMAP-projected space.
- Color of a point is the average of its domain colors (multi-domain
  pages get a blended hue). Pages without domain are gray.
- Domain pages are larger and labeled.
- Wikilink edges are drawn as light gray lines (off via --no-edges).

Pipeline parts:
- build_dataset: collect (name, vec, domains, links) for content pages
- compute_umap_coords: 4096-d → 2-d
- assign_domain_colors / blend_domain_colors: multi-domain RGB blend
- build_edges: undirected page-pair edges from wikilinks
- compute_statistics: counts, connectivity, semantic structure
- render_figure: Plotly Figure (lazy import)
- render_artifact_page: markdown for wiki/meta/kn-maps/

Usage:
    python3 bin/knowledge_map.py                # full output
    python3 bin/knowledge_map.py --no-edges     # skip edge overlay
    python3 bin/knowledge_map.py --no-page      # only files, skip artifact md
    python3 bin/knowledge_map.py --seed 7       # deterministic UMAP
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Make sibling modules importable when running as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))

from embed import (
    EmbedIndex,
    WIKI_EMBED_PATH,
    WIKI_ROOT,
    cosine,
    percentile,
)
from lint import (
    Page,
    _extract_wikilinks,
    _normalize_wiki_target,
    discover_pages,
)


# ────────────────────────────────────────────────────────────────────────
# Data model
# ────────────────────────────────────────────────────────────────────────


@dataclass
class PageInfo:
    name: str
    path: str
    page_type: str
    folder: str
    domains: list[str]
    vec: list[float] | None
    body_links: set[str] = field(default_factory=set)
    fm_links: set[str] = field(default_factory=set)
    inbound: set[str] = field(default_factory=set)


# ────────────────────────────────────────────────────────────────────────
# Dataset construction
# ────────────────────────────────────────────────────────────────────────


def _is_content_page(p: Page) -> bool:
    """Drop meta pages, wiki root files, and the artifact pages we generate."""
    if p.page_type == "meta":
        return False
    if p.folder in ("", "meta"):
        return False
    return True


def build_dataset(pages: list[Page], wiki_idx: EmbedIndex) -> list[PageInfo]:
    """Collect PageInfo for every content page.

    For each page extract:
    - domains (from frontmatter `domain:` field, normalized to basename)
    - body_links and fm_links (basenames of wiki targets, raw/ excluded)
    - vec (from embedding index; None if missing)

    After the first pass, walk again to fill `inbound` from accumulated outlinks.
    """
    content = [p for p in pages if _is_content_page(p)]
    infos: list[PageInfo] = []
    by_name: dict[str, PageInfo] = {}

    for p in content:
        domains = _extract_domains(p)
        body_links = {
            link for link in _extract_wikilinks(p.body or "")
            if not link.startswith("raw/")
        }
        fm_links = _extract_fm_related_links(p)
        info = PageInfo(
            name=p.name,
            path=p.path.as_posix(),
            page_type=p.page_type or "",
            folder=p.folder,
            domains=domains,
            vec=wiki_idx.get(p.name),
            body_links=body_links,
            fm_links=fm_links,
        )
        infos.append(info)
        by_name[p.name] = info

    # Inbound: for each link, if target is in our content set, increment.
    for info in infos:
        for tgt in info.body_links | info.fm_links:
            target = by_name.get(tgt)
            if target is not None:
                target.inbound.add(info.name)

    return infos


def _extract_domains(p: Page) -> list[str]:
    """Return list of domain wikilink basenames from `domain:` frontmatter field."""
    if p.fm is None:
        return []
    raw = p.fm.fields.get("domain")
    if not isinstance(raw, list):
        return []
    domains: list[str] = []
    for v in raw:
        if not isinstance(v, str):
            continue
        for link in _extract_wikilinks(v):
            if link.startswith("raw/"):
                continue
            domains.append(link)
    return domains


def _extract_fm_related_links(p: Page) -> set[str]:
    """Set of wiki link basenames from `related:` frontmatter (raw/ excluded)."""
    if p.fm is None:
        return set()
    raw = p.fm.fields.get("related")
    if not isinstance(raw, list):
        return set()
    out: set[str] = set()
    for v in raw:
        if not isinstance(v, str):
            continue
        for link in _extract_wikilinks(v):
            if not link.startswith("raw/"):
                out.add(link)
    return out


# ────────────────────────────────────────────────────────────────────────
# Domain → color
# ────────────────────────────────────────────────────────────────────────


def collect_domains(infos: list[PageInfo]) -> dict[str, int]:
    """Domain name → page count."""
    counts: Counter = Counter()
    for info in infos:
        for d in info.domains:
            counts[d] += 1
    return dict(counts)


def assign_domain_colors(domains: list[str], palette: list[str]) -> dict[str, str]:
    """Stable, deterministic domain → hex-color map. Sorted alphabetically
    so the same wiki always produces the same color assignment."""
    return {d: palette[i % len(palette)] for i, d in enumerate(sorted(domains))}


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    """'#aabbcc' or 'aabbcc' → (r, g, b)."""
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def blend_domain_colors(
    domains: list[str],
    domain_to_color: dict[str, str],
    default: str = "#cccccc",
) -> str:
    """Multi-domain page → averaged RGB. No domains or all-unknown → default."""
    if not domains:
        return default
    rgbs = [hex_to_rgb(domain_to_color[d]) for d in domains if d in domain_to_color]
    if not rgbs:
        return default
    avg = tuple(sum(c[i] for c in rgbs) // len(rgbs) for i in range(3))
    return rgb_to_hex(avg)


# ────────────────────────────────────────────────────────────────────────
# Edges
# ────────────────────────────────────────────────────────────────────────


def build_edges(infos: list[PageInfo]) -> list[tuple[int, int]]:
    """Undirected, deduped edges between page indices.

    A wikilink A→B and B→A both produce one edge (i, j) with i<j.
    Self-loops dropped.
    """
    name_to_idx = {info.name: i for i, info in enumerate(infos)}
    edges: set[tuple[int, int]] = set()
    for src, info in enumerate(infos):
        for tgt_name in info.body_links | info.fm_links:
            tgt = name_to_idx.get(tgt_name)
            if tgt is None or tgt == src:
                continue
            edge = (src, tgt) if src < tgt else (tgt, src)
            edges.add(edge)
    return sorted(edges)


# ────────────────────────────────────────────────────────────────────────
# Statistics
# ────────────────────────────────────────────────────────────────────────


def compute_statistics(infos: list[PageInfo]) -> dict[str, Any]:
    """Counts + connectivity + semantic structure summary for artifact page."""
    by_name = {info.name: info for info in infos}

    # ─── counts ───────────────────────────────────────────────
    type_counts = Counter(info.page_type for info in infos)
    domain_counts = collect_domains(infos)
    unassigned = sum(1 for info in infos if not info.domains)

    # ─── connectivity ─────────────────────────────────────────
    valid_outlinks = sum(
        sum(1 for tgt in (info.body_links | info.fm_links) if tgt in by_name)
        for info in infos
    )
    orphans = [info.name for info in infos if not info.inbound]

    most_connected: list[dict[str, Any]] = []
    if infos:
        max_inbound = max(len(info.inbound) for info in infos)
        if max_inbound > 0:
            most_connected = [
                {"name": info.name, "inbound": len(info.inbound)}
                for info in infos
                if len(info.inbound) == max_inbound
            ]

    # ─── semantic ────────────────────────────────────────────
    name_to_vec = {info.name: info.vec for info in infos if info.vec is not None}
    names = list(name_to_vec.keys())
    sims: list[tuple[str, str, float]] = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            sims.append((
                names[i], names[j],
                cosine(name_to_vec[names[i]], name_to_vec[names[j]]),
            ))
    sim_values = [s[2] for s in sims]

    tightest_pair = None
    if sims:
        a, b, s = max(sims, key=lambda x: x[2])
        tightest_pair = (a, b, round(s, 3))

    most_isolated = None
    if names:
        per_name_max: dict[str, float] = {n: 0.0 for n in names}
        for a, b, s in sims:
            if s > per_name_max[a]:
                per_name_max[a] = s
            if s > per_name_max[b]:
                per_name_max[b] = s
        n, m = min(per_name_max.items(), key=lambda x: x[1])
        most_isolated = (n, round(m, 3))

    # ─── per-domain coverage (avg internal cosine) ───────────
    domain_avg_cosine: dict[str, float] = {}
    for d in domain_counts:
        members = [info.name for info in infos if d in info.domains and info.vec is not None]
        if len(members) < 2:
            continue
        d_sims: list[float] = []
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                d_sims.append(cosine(name_to_vec[members[i]], name_to_vec[members[j]]))
        if d_sims:
            domain_avg_cosine[d] = round(sum(d_sims) / len(d_sims), 3)

    return {
        "type_counts": dict(type_counts),
        "domain_counts": domain_counts,
        "unassigned": unassigned,
        "valid_outlinks": valid_outlinks,
        "orphans": orphans,
        "most_connected": most_connected,
        "sim_count": len(sim_values),
        "sim_median": round(percentile(sim_values, 50), 3),
        "sim_p75": round(percentile(sim_values, 75), 3),
        "sim_p95": round(percentile(sim_values, 95), 3),
        "sim_max": round(max(sim_values), 3) if sim_values else 0.0,
        "tightest_pair": tightest_pair,
        "most_isolated": most_isolated,
        "domain_avg_cosine": domain_avg_cosine,
    }


# ────────────────────────────────────────────────────────────────────────
# Plotly figure (lazy import — viz deps optional for tests)
# ────────────────────────────────────────────────────────────────────────


def render_figure(
    infos: list[PageInfo],
    coords,
    edges: list[tuple[int, int]],
    domain_to_color: dict[str, str],
    show_edges: bool,
):
    """Build a Plotly figure. Returns plotly.graph_objects.Figure."""
    import plotly.graph_objects as go

    fig = go.Figure()

    # Edges first so they render behind nodes
    if show_edges and edges:
        edge_xs: list[float | None] = []
        edge_ys: list[float | None] = []
        for src, tgt in edges:
            edge_xs.extend([float(coords[src, 0]), float(coords[tgt, 0]), None])
            edge_ys.extend([float(coords[src, 1]), float(coords[tgt, 1]), None])
        fig.add_trace(go.Scatter(
            x=edge_xs, y=edge_ys,
            mode="lines",
            line=dict(color="lightgray", width=0.5),
            opacity=0.6,
            hoverinfo="skip",
            showlegend=False,
            name="wikilinks",
        ))

    # Group points by page_type so the legend shows types, not individual pages
    by_type: dict[str, list[int]] = {}
    for i, info in enumerate(infos):
        by_type.setdefault(info.page_type or "other", []).append(i)

    type_size = {"domain": 18, "idea": 11, "entity": 11, "question": 11}
    type_order = ["domain", "idea", "entity", "question", "other"]

    for ptype in type_order:
        idxs = by_type.get(ptype)
        if not idxs:
            continue
        xs = [float(coords[i, 0]) for i in idxs]
        ys = [float(coords[i, 1]) for i in idxs]
        colors = [
            blend_domain_colors(infos[i].domains, domain_to_color)
            for i in idxs
        ]
        sizes = [type_size.get(ptype, 11) for _ in idxs]
        labels = [infos[i].name if ptype == "domain" else "" for i in idxs]
        hover = [
            f"<b>{infos[i].name}</b><br>"
            f"type: {infos[i].page_type or '—'}<br>"
            f"domains: {', '.join(infos[i].domains) or '(none)'}<br>"
            f"inbound: {len(infos[i].inbound)} | outbound: "
            f"{len(infos[i].body_links | infos[i].fm_links)}"
            for i in idxs
        ]
        fig.add_trace(go.Scatter(
            x=xs, y=ys,
            mode="markers+text" if ptype == "domain" else "markers",
            marker=dict(size=sizes, color=colors, line=dict(width=1, color="#444")),
            text=labels,
            textposition="top center",
            textfont=dict(size=12, color="#222"),
            hovertext=hover,
            hoverinfo="text",
            name=ptype,
        ))

    fig.update_layout(
        title=dict(text="Wiki Knowledge Map (UMAP)", x=0.5, xanchor="center"),
        xaxis=dict(showticklabels=False, showgrid=False, zeroline=False, visible=False),
        yaxis=dict(showticklabels=False, showgrid=False, zeroline=False, visible=False),
        plot_bgcolor="white",
        paper_bgcolor="white",
        width=1200, height=900,
        margin=dict(l=20, r=20, t=60, b=20),
        legend=dict(title="Page type", borderwidth=1, bordercolor="#ccc"),
    )
    return fig


# ────────────────────────────────────────────────────────────────────────
# Artifact markdown page
# ────────────────────────────────────────────────────────────────────────


def render_artifact_page(
    stats: dict[str, Any],
    image_filename: str,
    html_filename: str,
    generated_at: str,
) -> str:
    """Render the wiki/meta/knowledge-map-YYYY-MM-DD.md artifact."""
    total_pages = sum(stats["type_counts"].values())
    # Manual plural map — "entity" → "entities", not "entitys"
    _PLURAL = {
        "idea": "ideas", "entity": "entities",
        "question": "questions", "domain": "domains",
    }
    type_breakdown = ", ".join(
        f"{n} {_PLURAL.get(t, t + 's') if n != 1 else t}"
        for t, n in sorted(stats["type_counts"].items())
    )

    lines = [
        "---",
        "type: meta",
        f"generated: {generated_at}",
        f"pages_total: {total_pages}",
        f"domains_count: {len(stats['domain_counts'])}",
        "---",
        "",
        f"# Knowledge Map — {generated_at[:10]}",
        "",
        f"![[{image_filename}]]",
        "",
        f"Интерактивная HTML-версия: `_attachments/{html_filename}` (открой в браузере для zoom/pan/hover).",
        "",
        "## Counts",
        "",
        f"- Pages: {total_pages} ({type_breakdown})",
    ]

    if stats["domain_counts"]:
        domain_str = ", ".join(
            f"[[{d}]] ({n})"
            for d, n in sorted(stats["domain_counts"].items(), key=lambda x: -x[1])
        )
        lines.append(f"- Domains: {domain_str}")
    lines.append(f"- Unassigned (no domain): {stats['unassigned']}")

    lines.extend([
        "",
        "## Connectivity",
        "",
        f"- Valid wikilinks: {stats['valid_outlinks']}",
        f"- Orphan pages: {len(stats['orphans'])}",
    ])
    if stats["most_connected"]:
        mc_str = ", ".join(
            f"[[{p['name']}]] ({p['inbound']} inbound)"
            for p in stats["most_connected"][:3]
        )
        lines.append(f"- Most connected: {mc_str}")

    lines.extend([
        "",
        "## Semantic structure",
        "",
        f"- Pairs analyzed: {stats['sim_count']}",
        f"- Pairwise cosine: median {stats['sim_median']:.3f}, "
        f"p75 {stats['sim_p75']:.3f}, p95 {stats['sim_p95']:.3f}, max {stats['sim_max']:.3f}",
    ])
    if stats["tightest_pair"]:
        a, b, s = stats["tightest_pair"]
        lines.append(f"- Tightest pair: [[{a}]] ↔ [[{b}]] (cosine {s:.3f})")
    if stats["most_isolated"]:
        n, m = stats["most_isolated"]
        lines.append(f"- Most isolated: [[{n}]] (max similarity to others: {m:.3f})")

    if stats["domain_avg_cosine"]:
        lines.extend([
            "",
            "## Domain coverage",
            "",
            "Avg internal cosine = средняя попарная косинусная близость страниц одного домена. "
            "Высокое значение → плотный когерентный домен, низкое → домен размазан.",
            "",
            "| Domain | Pages | Avg internal cosine |",
            "|---|---|---|",
        ])
        for d in sorted(
            stats["domain_avg_cosine"],
            key=lambda x: -stats["domain_counts"].get(x, 0),
        ):
            lines.append(
                f"| [[{d}]] | {stats['domain_counts'][d]} | "
                f"{stats['domain_avg_cosine'][d]:.3f} |"
            )

    return "\n".join(lines) + "\n"


# ────────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--no-edges", action="store_true",
                    help="skip wikilink edge overlay")
    ap.add_argument("--no-page", action="store_true",
                    help="skip generating wiki/meta/kn-maps/knowledge-map-*.md")
    ap.add_argument("--seed", type=int, default=42,
                    help="UMAP random_state for reproducibility (default: 42)")
    ap.add_argument("--out-dir", type=Path, default=Path("_attachments"),
                    help="output directory for HTML/PNG (default: _attachments)")
    args = ap.parse_args()

    # Lazy viz imports — let helper-only tests run without these installed
    try:
        import numpy as np
        import plotly.colors as pc
        import umap
    except ImportError as e:
        print(f"ERROR: missing dependency '{e.name}'", file=sys.stderr)
        print("Run: pip3 install -r bin/requirements.txt", file=sys.stderr)
        return 2

    # 1. Discover content + load embeddings
    pages = discover_pages()
    if not pages:
        print("no wiki pages found", file=sys.stderr)
        return 0

    wiki_idx = EmbedIndex(WIKI_EMBED_PATH)
    wiki_idx.load()
    if not wiki_idx.items:
        print(
            f"error: wiki embeddings empty at {WIKI_EMBED_PATH}",
            file=sys.stderr,
        )
        print("Run: python3 bin/embed.py update", file=sys.stderr)
        return 2

    # 2. Build dataset
    infos = build_dataset(pages, wiki_idx)
    infos_with_vecs = [info for info in infos if info.vec is not None]

    if len(infos_with_vecs) < 3:
        print(
            f"need at least 3 content pages with embeddings, have "
            f"{len(infos_with_vecs)}",
            file=sys.stderr,
        )
        return 2

    print(
        f"discovered: {len(infos)} content pages, "
        f"{len(infos_with_vecs)} with embeddings"
    )

    # 3. UMAP
    vecs = np.array([info.vec for info in infos_with_vecs])
    n_neighbors = min(15, len(infos_with_vecs) - 1)
    reducer = umap.UMAP(
        n_components=2, n_neighbors=n_neighbors, random_state=args.seed,
    )
    coords = reducer.fit_transform(vecs)

    # 4. Edges
    edges = build_edges(infos_with_vecs) if not args.no_edges else []

    # 5. Domain colors
    domain_counts = collect_domains(infos_with_vecs)
    domain_to_color = assign_domain_colors(
        list(domain_counts.keys()), pc.qualitative.Plotly,
    )

    # 6. Render figure
    fig = render_figure(
        infos_with_vecs, coords, edges, domain_to_color,
        show_edges=not args.no_edges,
    )

    # 7. Output files
    args.out_dir.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    base = f"knowledge-map-{today}"
    html_path = args.out_dir / f"{base}.html"
    png_path = args.out_dir / f"{base}.png"

    fig.write_html(str(html_path), include_plotlyjs="cdn")
    print(f"wrote {html_path}")

    try:
        fig.write_image(str(png_path), scale=2, width=1600, height=1200)
        print(f"wrote {png_path}")
    except Exception as e:
        print(f"WARN: PNG export failed: {e}", file=sys.stderr)
        print("  hint: pip install kaleido==0.2.1", file=sys.stderr)

    # 8. Statistics + artifact page
    if not args.no_page:
        stats = compute_statistics(infos_with_vecs)
        generated_at = dt.datetime.now().isoformat(timespec="seconds")
        page_md = render_artifact_page(
            stats, png_path.name, html_path.name, generated_at,
        )
        artifact_dir = WIKI_ROOT / "meta" / "kn-maps"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / f"{base}.md"
        artifact_path.write_text(page_md, encoding="utf-8")
        print(f"wrote {artifact_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
