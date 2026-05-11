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
from static_lint import (
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


def generate_distinct_palette(
    n: int,
    *,
    hue_offset_deg: float = 220.0,
    saturation: float = 0.70,
    lightness: float = 0.65,
) -> list[str]:
    """Generate N maximally-distinguishable hex colors via HSL color wheel.

    Hues are evenly spaced (360/N degrees apart), so adjacent legend entries
    are diametrically (or near-diametrically) opposite on the color wheel.

    Defaults are tuned for dark backgrounds:
    - lightness 0.65 — bright enough to read on #0F1419, not pastel
    - saturation 0.70 — vivid enough for distinction, not neon
    - hue_offset 220° — start at blue (modern/tech look) rather than red

    Examples for n=4 with default offset:
        220°=blue, 310°=pink, 40°=amber, 130°=green
    """
    import colorsys
    if n <= 0:
        return []
    palette: list[str] = []
    for i in range(n):
        hue = ((hue_offset_deg + i * 360.0 / n) % 360.0) / 360.0
        r, g, b = colorsys.hls_to_rgb(hue, lightness, saturation)
        palette.append(
            "#{:02x}{:02x}{:02x}".format(
                round(r * 255), round(g * 255), round(b * 255),
            )
        )
    return palette


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


_BLEND_LIGHTNESS_SHIFT = -0.18   # darker than pure domain colors
_BLEND_SATURATION_SHIFT = -0.05  # very slightly muted


def blend_domain_colors(
    domains: list[str],
    domain_to_color: dict[str, str],
    default: str = "#cccccc",
) -> str:
    """Multi-domain page → blended color in HSL space. Single domain → its
    color. None or all-unknown → default.

    Why HSL and not RGB. RGB-averaging of two complementary hues (e.g.,
    yellow + blue at 180°) cancels chroma → gray. HSL hue averaging via
    vector sum on the unit circle preserves color identity.

    Why blends are darker than pure domain colors. With N evenly-spaced
    domain hues, midpoints between some pairs land exactly on another
    domain's hue (e.g., for 4 domains at 90° steps, midpoint of opposite
    domains = third domain's hue). Pure-hue collision would visually merge
    multi-domain pages with the wrong cluster. We shift the blend's
    lightness down (-0.18) so the blend stays visually distinct from any
    pure domain color even when their hues coincide.

    Hue-cancellation edge case: when vector sum has near-zero magnitude
    (exactly opposite hues), atan2 is undefined. Fall back to linear
    midpoint of sorted hues → e.g., yellow(40°)+blue(220°) → green(130°).
    """
    if not domains:
        return default
    hex_codes = [domain_to_color[d] for d in domains if d in domain_to_color]
    if not hex_codes:
        return default
    if len(hex_codes) == 1:
        return hex_codes[0]

    import colorsys
    import math

    hsls = []
    for c in hex_codes:
        r, g, b = hex_to_rgb(c)
        h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        hsls.append((h, l, s))

    avg_l = sum(item[1] for item in hsls) / len(hsls)
    avg_s = sum(item[2] for item in hsls) / len(hsls)

    sin_sum = sum(math.sin(2 * math.pi * h) for h, _, _ in hsls)
    cos_sum = sum(math.cos(2 * math.pi * h) for h, _, _ in hsls)
    magnitude = math.sqrt(sin_sum * sin_sum + cos_sum * cos_sum)

    if magnitude < 0.05 * len(hsls):
        sorted_hues = sorted(h for h, _, _ in hsls)
        avg_h = sum(sorted_hues) / len(sorted_hues)
    else:
        avg_h = math.atan2(sin_sum, cos_sum) / (2 * math.pi)
        if avg_h < 0:
            avg_h += 1

    # Apply blend offset (only multi-domain, since len > 1 here)
    avg_l = max(0.0, min(1.0, avg_l + _BLEND_LIGHTNESS_SHIFT))
    avg_s = max(0.0, min(1.0, avg_s + _BLEND_SATURATION_SHIFT))

    r, g, b = colorsys.hls_to_rgb(avg_h, avg_l, avg_s)
    return rgb_to_hex((round(r * 255), round(g * 255), round(b * 255)))


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
    """Build a Plotly figure with domain-grouped legend.

    Legend groups points by **primary domain** (first in `info.domains`)
    so the colored swatches in the legend honestly correspond to colors
    on the map. Page type controls only marker size (domain hubs larger),
    documented in the artifact-page caption — not in the legend.
    """
    import plotly.graph_objects as go

    fig = go.Figure()

    _NO_DOMAIN_KEY = "_no_domain"
    _NO_DOMAIN_COLOR = "#6B7280"  # neutral gray-500 (visible on dark bg)
    _NO_DOMAIN_LABEL = "без домена"

    _TYPE_LABEL = {
        "domain": "домен", "idea": "идея",
        "entity": "сущность", "question": "вопрос",
    }

    # Dark-theme color tokens (Tailwind-inspired)
    _BG = "#0F1419"          # plot/paper background
    _TEXT = "#E5E7EB"        # primary light gray text
    _TEXT_MUTED = "#9CA3AF"  # muted text
    _GRID = "rgba(255,255,255,0.04)"
    _OUTLINE = "#1F2937"     # marker stroke on dark bg
    _OUTLINE_HUB = "#F9FAFB" # white-ish stroke for domain hubs
    _EDGE = "rgba(129,140,248,0.18)"  # subtle indigo edges

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
            line=dict(color=_EDGE, width=0.6),
            hoverinfo="skip",
            showlegend=False,
            name="wikilinks",
        ))

    # ─── group nodes by primary domain ──────────────────────────────
    # Primary domain = first item in info.domains (= first wikilink in the
    # page's `domain:` frontmatter field). Schema convention: list from
    # specific to general (e.g., [Reinforcement Learning, Machine Learning]).
    # The lint check `domain-order` enforces this ordering across the wiki.
    by_primary: dict[str, list[int]] = {}
    for i, info in enumerate(infos):
        primary = info.domains[0] if info.domains else _NO_DOMAIN_KEY
        by_primary.setdefault(primary, []).append(i)

    # Stable order: domains by member count desc, then alphabetical;
    # "без домена" last.
    domain_order = sorted(
        (k for k in by_primary if k != _NO_DOMAIN_KEY),
        key=lambda d: (-len(by_primary[d]), d),
    )
    if _NO_DOMAIN_KEY in by_primary:
        domain_order.append(_NO_DOMAIN_KEY)

    type_size = {"domain": 26, "idea": 14, "entity": 14, "question": 14}

    for domain in domain_order:
        idxs = by_primary[domain]
        legend_name = _NO_DOMAIN_LABEL if domain == _NO_DOMAIN_KEY else domain
        legend_clean_color = (
            domain_to_color.get(domain, _NO_DOMAIN_COLOR)
            if domain != _NO_DOMAIN_KEY else _NO_DOMAIN_COLOR
        )

        xs = [float(coords[i, 0]) for i in idxs]
        ys = [float(coords[i, 1]) for i in idxs]
        # Per-point color: blended RGB if multi-domain, primary color otherwise.
        # Visually preserves the "between clusters" hue for boundary pages.
        point_colors = [
            blend_domain_colors(
                infos[i].domains, domain_to_color, default=_NO_DOMAIN_COLOR,
            )
            for i in idxs
        ]
        sizes = [type_size.get(infos[i].page_type, 12) for i in idxs]
        line_widths = [2 if infos[i].page_type == "domain" else 0.8 for i in idxs]
        line_colors = [
            _OUTLINE_HUB if infos[i].page_type == "domain" else _OUTLINE
            for i in idxs
        ]
        labels = [
            infos[i].name if infos[i].page_type == "domain" else ""
            for i in idxs
        ]
        hover = [
            f"<b>{infos[i].name}</b><br>"
            f"тип: {_TYPE_LABEL.get(infos[i].page_type or '', infos[i].page_type or '—')}<br>"
            f"домены: {', '.join(infos[i].domains) or '—'}<br>"
            f"входящих: {len(infos[i].inbound)} · "
            f"исходящих: {len(infos[i].body_links | infos[i].fm_links)}"
            for i in idxs
        ]

        # Single trace per domain — uniform color = primary domain color.
        # No blending: first domain wins both in the legend AND in the dot.
        fig.add_trace(go.Scatter(
            x=xs, y=ys,
            mode="markers+text" if any(labels) else "markers",
            marker=dict(
                size=sizes,
                color=legend_clean_color,
                line=dict(width=line_widths, color=line_colors),
                opacity=0.92,
            ),
            text=labels,
            textposition="top center",
            textfont=dict(size=13, color=_TEXT, family="Inter, sans-serif"),
            hovertext=hover,
            hoverinfo="text",
            name=legend_name,
        ))

    # ─── layout ─────────────────────────────────────────────────────
    # Modern dark-dashboard styling (Stripe / Linear / Notion vibe):
    # - dark background with desaturated accent colors
    # - Inter font, light-gray text
    # - left-aligned title, horizontal legend on top
    # - axes invisible, equal aspect via scaleanchor preserves distances
    # - figure is responsive (autosize) — fills iframe/container
    fig.update_layout(
        title=dict(
            text="<b>Карта знаний wiki</b>",
            x=0.03, xanchor="left",
            y=0.97, yanchor="top",
            font=dict(size=20, family="Inter, sans-serif", color=_TEXT),
            pad=dict(t=10, l=10),
        ),
        font=dict(family="Inter, sans-serif", color=_TEXT, size=12),
        xaxis=dict(visible=False),
        yaxis=dict(
            visible=False,
            scaleanchor="x", scaleratio=1,
        ),
        paper_bgcolor=_BG,
        plot_bgcolor=_BG,
        autosize=True,
        margin=dict(l=40, r=20, t=80, b=40),
        legend=dict(
            title=dict(text=""),
            orientation="h",
            x=0.5, xanchor="center",
            y=-0.02, yanchor="top",
            bgcolor="rgba(0,0,0,0)",
            font=dict(size=12, color=_TEXT),
            itemsizing="constant",
        ),
        hoverlabel=dict(
            bgcolor="rgba(15,20,25,0.95)",
            bordercolor="#374151",
            font=dict(family="Inter, sans-serif", color=_TEXT, size=12),
        ),
        modebar=dict(
            bgcolor="rgba(0,0,0,0)",
            color=_TEXT_MUTED,
            activecolor=_TEXT,
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
    iframe_src: str | None = None,
) -> str:
    """Render the wiki/meta/kn-maps/knowledge-map-YYYY-MM-DD.md artifact.

    Output is in Russian, uses markdown tables for stats, and embeds the
    interactive Plotly HTML via an iframe (Obsidian renders it in reading
    mode). No PNG embed — the iframe is the canonical view.

    iframe_src is the URL the iframe points to. Caller should construct it
    via Obsidian's `app://local/<absolute-path>` scheme — Obsidian's iframe
    sandbox blocks file://, relative paths, and (in some configs)
    app://obsidian.md/, but app://local/ works for local files.
    """
    total_pages = sum(stats["type_counts"].values())

    # Russian labels for page types
    _TYPE_LABEL = {
        "idea": "идеи", "entity": "сущности",
        "question": "вопросы", "domain": "домены",
    }

    # Default fallback if caller doesn't provide an explicit URL
    if iframe_src is None:
        iframe_src = f"app://obsidian.md/_attachments/{html_filename}"

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
        f'<iframe src="{iframe_src}" '
        'style="width:100%; aspect-ratio: 4 / 3; border:1px solid #ccc; '
        'border-radius:6px; display:block;"></iframe>',
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
        "- **Цвет** — **первый домен** в `domain:` страницы. По convention "
        "wiki, домены перечисляются от частного к общему, поэтому первый — "
        "самый специфичный. Например, у страницы с "
        "`[Reinforcement Learning, Machine Learning]` цвет — RL. "
        "Полный список доменов виден в hover. Серый — страница без домена.",
        "- **Размер** — тип страницы: domain-страницы (хабы) крупнее остальных. "
        "Тип в легенде не показан, чтобы не путать с цветом.",
        "- **Линии** — wikilinks между страницами (полупрозрачные). "
        "Видно где явные связи совпадают с семантическими, а где расходятся.",
        "",
        "Когерентный кластер — много точек одного цвета рядом. Если страница "
        "оторвалась от своего цветового кластера — её эмбеддинг ушёл в чужую "
        "семантическую область (потенциальный сигнал для ingest или lint). "
        "Изолированная точка — семантически уникальная страница (или сирота).",
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
    # HSL-evenly-spaced palette: maximum perceptual distinction between any
    # number of domains. Tuned for dark background — light enough to read,
    # not pastel. See generate_distinct_palette() for parameters.
    palette = generate_distinct_palette(max(len(domain_counts), 1))
    domain_to_color = assign_domain_colors(list(domain_counts.keys()), palette)

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
    # responsive=True makes the figure fill its container (the iframe).
    fig.write_html(
        str(html_path),
        include_plotlyjs="inline",
        config={"responsive": True},
    )
    print(f"wrote {html_path}")

    # 8. Statistics + artifact page
    if not args.no_page:
        stats = compute_statistics(infos_with_vecs)
        generated_at = dt.datetime.now().isoformat(timespec="seconds")
        # For local HTML embed, file:///<absolute-path> works reliably in
        # Obsidian's iframe sandbox (no CSP restrictions). Path is machine-
        # specific (encoded into markdown), but artifact pages are local-only
        # auto-generated reports anyway. URL-encode special characters.
        from urllib.parse import quote
        abs_html = html_path.resolve()
        # ?v=<timestamp> cache-buster — Obsidian / Electron caches local
        # iframes aggressively, and without this the iframe sticks on the
        # previous render even after the HTML file is regenerated.
        cache_buster = dt.datetime.now().strftime("%Y%m%d%H%M%S")
        iframe_src = f"file://{quote(str(abs_html))}?v={cache_buster}"
        page_md = render_artifact_page(
            stats, html_path.name, generated_at, iframe_src=iframe_src,
        )
        artifact_dir = WIKI_ROOT / "meta" / "kn-maps"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / f"{base}.md"
        artifact_path.write_text(page_md, encoding="utf-8")
        print(f"wrote {artifact_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
