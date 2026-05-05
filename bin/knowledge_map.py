#!/usr/bin/env python3
"""Knowledge map: UMAP-projected wiki visualization with statistics.

Generates two artifacts on each run:

    _attachments/knowledge-map-YYYY-MM-DD.html       interactive Plotly viz
    wiki/meta/kn-maps/knowledge-map-YYYY-MM-DD.md    markdown with stats + iframe

The .md page is versioned (timestamp in filename) so successive runs form
a history of wiki growth, like lint-report-*.md. The HTML is the canonical
view — embedded into the .md via iframe so Obsidian renders the
interactive Plotly figure directly in reading mode.

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
    """Parse a color string into (r, g, b) ints in [0, 255].

    Accepts:
    - hex with hash:    "#aabbcc"
    - hex without hash: "aabbcc"
    - rgb function:     "rgb(170, 187, 204)" (Plotly's qualitative.Bold etc.)
    """
    s = h.strip()
    if s.startswith("rgb"):
        # Strip the "rgb(" prefix and trailing ")", split on commas
        inner = s[s.index("(") + 1 : s.rindex(")")]
        parts = [int(p.strip()) for p in inner.split(",")]
        return parts[0], parts[1], parts[2]
    s = s.lstrip("#")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


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

    # Russian legend labels for page types
    _TYPE_LEGEND = {
        "domain": "домены (хабы)",
        "idea": "идеи",
        "entity": "сущности",
        "question": "вопросы",
        "other": "прочее",
    }

    # ─── edges first (render behind nodes) ──────────────────────────
    if show_edges and edges:
        edge_xs: list[float | None] = []
        edge_ys: list[float | None] = []
        for src, tgt in edges:
            edge_xs.extend([float(coords[src, 0]), float(coords[tgt, 0]), None])
            edge_ys.extend([float(coords[src, 1]), float(coords[tgt, 1]), None])
        fig.add_trace(go.Scatter(
            x=edge_xs, y=edge_ys,
            mode="lines",
            line=dict(color="rgba(100,100,100,0.25)", width=0.7),
            hoverinfo="skip",
            showlegend=False,
            name="wikilinks",
        ))

    # ─── nodes grouped by type ──────────────────────────────────────
    by_type: dict[str, list[int]] = {}
    for i, info in enumerate(infos):
        by_type.setdefault(info.page_type or "other", []).append(i)

    # Larger markers for visibility; domain hubs noticeably bigger
    type_size = {"domain": 26, "idea": 14, "entity": 14, "question": 14, "other": 12}
    type_order = ["idea", "entity", "question", "other", "domain"]  # domain last → on top

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
        sizes = [type_size.get(ptype, 12) for _ in idxs]
        labels = [infos[i].name if ptype == "domain" else "" for i in idxs]
        hover = [
            f"<b>{infos[i].name}</b><br>"
            f"тип: {_TYPE_LEGEND.get(infos[i].page_type or 'other', infos[i].page_type or '—')}<br>"
            f"домены: {', '.join(infos[i].domains) or '—'}<br>"
            f"входящих: {len(infos[i].inbound)} · "
            f"исходящих: {len(infos[i].body_links | infos[i].fm_links)}"
            for i in idxs
        ]
        fig.add_trace(go.Scatter(
            x=xs, y=ys,
            mode="markers+text" if ptype == "domain" else "markers",
            marker=dict(
                size=sizes,
                color=colors,
                line=dict(
                    width=2 if ptype == "domain" else 1,
                    color="#222" if ptype == "domain" else "#555",
                ),
                opacity=0.95,
            ),
            text=labels,
            textposition="top center",
            textfont=dict(size=14, color="#111", family="Inter, system-ui, sans-serif"),
            hovertext=hover,
            hoverinfo="text",
            name=_TYPE_LEGEND.get(ptype, ptype),
        ))

    # ─── layout ─────────────────────────────────────────────────────
    fig.update_layout(
        title=dict(
            text="<b>Карта знаний wiki</b> · UMAP-проекция эмбеддингов",
            x=0.5, xanchor="center",
            font=dict(size=18, family="Inter, system-ui, sans-serif", color="#111"),
        ),
        xaxis=dict(showticklabels=False, showgrid=False, zeroline=False, visible=False),
        yaxis=dict(
            showticklabels=False, showgrid=False, zeroline=False, visible=False,
            scaleanchor="x", scaleratio=1,  # equal aspect ratio
        ),
        plot_bgcolor="#fafafa",
        paper_bgcolor="white",
        width=1200, height=900,
        margin=dict(l=30, r=30, t=70, b=30),
        legend=dict(
            title=dict(text="<b>Тип страницы</b>"),
            bgcolor="rgba(255,255,255,0.9)",
            borderwidth=1, bordercolor="#ccc",
            x=0.99, y=0.99, xanchor="right", yanchor="top",
            font=dict(size=12),
        ),
        hoverlabel=dict(
            bgcolor="white",
            bordercolor="#888",
            font=dict(size=13, family="Inter, system-ui, sans-serif"),
        ),
    )
    return fig


# ────────────────────────────────────────────────────────────────────────
# Artifact markdown page
# ────────────────────────────────────────────────────────────────────────


def render_artifact_page(
    stats: dict[str, Any],
    html_filename: str,
    generated_at: str,
) -> str:
    """Render the wiki/meta/kn-maps/knowledge-map-YYYY-MM-DD.md artifact.

    Output is in Russian, uses markdown tables for stats, and embeds the
    interactive Plotly HTML via an iframe (Obsidian renders it in reading
    mode). No PNG embed — the iframe is the canonical view.
    """
    total_pages = sum(stats["type_counts"].values())

    # Russian labels for page types
    _TYPE_LABEL = {
        "idea": "идеи", "entity": "сущности",
        "question": "вопросы", "domain": "домены",
    }

    iframe_src = f"../../../_attachments/{html_filename}"

    lines: list[str] = [
        "---",
        "type: meta",
        f"generated: {generated_at}",
        f"pages_total: {total_pages}",
        f"domains_count: {len(stats['domain_counts'])}",
        "---",
        "",
        f"# Карта знаний — {generated_at[:10]}",
        "",
        "## Интерактивная карта",
        "",
        f'<iframe src="{iframe_src}" width="100%" height="800" '
        'style="border:1px solid #ccc; border-radius:6px;"></iframe>',
        "",
        f"Если iframe не отобразился, открой `_attachments/{html_filename}` "
        "в браузере напрямую — там доступны zoom, pan и hover с подробностями "
        "о каждой странице.",
        "",
        "## Как читать",
        "",
        "Каждая точка — одна wiki-страница. Координаты — двумерная "
        "**UMAP-проекция** эмбеддинга страницы (4096-мерный вектор → 2D). "
        "Чем ближе точки на карте, тем семантически ближе страницы по эмбеддингу "
        "(не по wikilink-связям).",
        "",
        "- **Цвет** — домен страницы (`domain:` во frontmatter). "
        "Если доменов несколько — цвет усреднён по RGB. Серый — без домена.",
        "- **Размер** — тип страницы: domain-страницы (хабы) крупнее остальных.",
        "- **Линии** — wikilinks между страницами (полупрозрачные). "
        "Видно где явные связи совпадают с семантическими, а где расходятся.",
        "",
        "Кластер близких точек одного цвета — когерентный домен. Точки "
        "на стыке цветов — мульти-доменные страницы. Изолированная точка "
        "— семантически уникальная страница (или сирота).",
        "",
        "## Счётчики",
        "",
        "| Тип | Количество |",
        "|---|---:|",
    ]
    for t, n in sorted(stats["type_counts"].items()):
        label = _TYPE_LABEL.get(t, t)
        lines.append(f"| {label} | {n} |")
    lines.append(f"| без домена | {stats['unassigned']} |")
    lines.append(f"| **всего** | **{total_pages}** |")

    if stats["domain_counts"]:
        lines.extend([
            "",
            "## Домены",
            "",
            "| Домен | Страниц |",
            "|---|---:|",
        ])
        for d, n in sorted(stats["domain_counts"].items(), key=lambda x: -x[1]):
            lines.append(f"| [[{d}]] | {n} |")

    lines.extend([
        "",
        "## Связность",
        "",
        "| Метрика | Значение |",
        "|---|---:|",
        f"| Wikilinks (валидных) | {stats['valid_outlinks']} |",
        f"| Страниц-сирот | {len(stats['orphans'])} |",
    ])
    if stats["most_connected"]:
        top = stats["most_connected"][0]
        lines.append(
            f"| Самая связанная | [[{top['name']}]] ({top['inbound']} входящих) |"
        )

    lines.extend([
        "",
        "## Семантическая структура",
        "",
        "Распределение попарных косинусных близостей всех wiki-страниц "
        "с эмбеддингами.",
        "",
        "| Метрика | Значение |",
        "|---|---:|",
        f"| Пар проанализировано | {stats['sim_count']} |",
        f"| Медиана | {stats['sim_median']:.3f} |",
        f"| 75-й перцентиль | {stats['sim_p75']:.3f} |",
        f"| 95-й перцентиль | {stats['sim_p95']:.3f} |",
        f"| Максимум | {stats['sim_max']:.3f} |",
    ])
    if stats["tightest_pair"]:
        a, b, s = stats["tightest_pair"]
        lines.append(f"| Самая близкая пара | [[{a}]] ↔ [[{b}]] ({s:.3f}) |")
    if stats["most_isolated"]:
        n, m = stats["most_isolated"]
        lines.append(f"| Самая изолированная | [[{n}]] (max близость {m:.3f}) |")

    if stats["domain_avg_cosine"]:
        lines.extend([
            "",
            "## Связность доменов",
            "",
            "**Avg internal cosine** — средняя попарная близость страниц одного "
            "домена. Высокое значение (>0.6) — плотный когерентный кластер. "
            "Низкое — домен размазан по нескольким темам, возможно стоит разбить "
            "на под-домены.",
            "",
            "| Домен | Страниц | Avg internal cosine |",
            "|---|---:|---:|",
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
    # Bold palette: more saturated than default Plotly, better for category
    # distinction at small marker sizes.
    domain_to_color = assign_domain_colors(
        list(domain_counts.keys()), pc.qualitative.Bold,
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

    # Embed plotly.js inline so the HTML is self-contained — Obsidian's
    # iframe sandbox blocks remote CDN fetches in some configurations.
    fig.write_html(str(html_path), include_plotlyjs="inline")
    print(f"wrote {html_path}")

    # 8. Statistics + artifact page
    if not args.no_page:
        stats = compute_statistics(infos_with_vecs)
        generated_at = dt.datetime.now().isoformat(timespec="seconds")
        page_md = render_artifact_page(stats, html_path.name, generated_at)
        artifact_dir = WIKI_ROOT / "meta" / "kn-maps"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / f"{base}.md"
        artifact_path.write_text(page_md, encoding="utf-8")
        print(f"wrote {artifact_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
