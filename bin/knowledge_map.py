#!/usr/bin/env python3
"""Knowledge map: dual-view wiki visualization with statistics.

Generates three artifacts on each run:

    _attachments/knowledge-map-YYYY-MM-DD.html       Cytoscape (UMAP-pinned)
    _attachments/wiki-graph-YYYY-MM-DD.html          Cytoscape (fcose-layout)
    wiki/meta/kn-maps/knowledge-map-YYYY-MM-DD.md    markdown with stats + iframes

Both HTMLs run on the same Cytoscape.js stack (vendored in bin/vendor/) —
the difference is layout, not engine:

- knowledge-map: positions pinned to UMAP coordinates → proximity on
  screen = semantic similarity. Louvain compound parents disabled to keep
  the semantic reading clean.
- wiki-graph: fcose force-directed layout → proximity on screen =
  densely linked. Compound parents make Louvain communities visually
  obvious; bridge nodes get a gold-haloed star shape.

The .md page embeds both as iframes (via file:// URLs that Obsidian's
iframe sandbox accepts) and adds counts, connectivity, Louvain diagnostics,
and semantic-similarity distributions. Versioned filenames build a history
of wiki growth, like lint-report-*.md.

Pipeline parts:
- build_dataset: collect (name, vec, domains, links) for content pages
- compute_umap_coords: 4096-d → 2-d
- assign_domain_colors / blend_domain_colors: multi-domain RGB blend
- build_edges: undirected page-pair edges from wikilinks
- compute_statistics: counts, connectivity, semantic structure
- compute_graph_structure: Louvain communities + bridges + sparse clusters
- wiki_graph.render_cytoscape_html: shared HTML renderer (preset / fcose)
- render_artifact_page: combined markdown for wiki/meta/kn-maps/

Usage:
    python3 bin/knowledge_map.py                # both HTMLs + markdown
    python3 bin/knowledge_map.py --no-edges     # skip edge overlay on UMAP
    python3 bin/knowledge_map.py --no-graph     # skip force-directed HTML
    python3 bin/knowledge_map.py --no-page      # only HTMLs, skip markdown
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
    - rgb function:     "rgb(170, 187, 204)" (CSS-style accepted by some palettes)
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
# Graph topology: Louvain communities, bridge nodes, sparse clusters
# ────────────────────────────────────────────────────────────────────────
#
# Topological signal complements the semantic UMAP picture. UMAP groups
# pages by embedding similarity — what they're "about". Louvain groups
# them by who-cites-whom — how the wiki is actually wired. Divergence
# between the two views is informative.
#
# Two derived diagnostics:
# - Bridge nodes: pages whose wikilinks span multiple communities.
#   Participation coefficient quantifies it; high P + high degree = a hub
#   carrying cross-area connectivity. Improving such a page lifts several
#   areas at once.
# - Sparse communities: clusters Louvain found, but whose members barely
#   link to each other internally (cohesion below threshold). The topic is
#   implicitly there, but cross-links are missing — an under-developed area.
#
# All thresholds are conservative so this is silent on small/healthy vaults.

_LOUVAIN_SEED = 42
_MIN_COMMUNITY_SIZE = 3              # below this, communities are not surfaced
_TOP_BRIDGES = 5
_SPARSE_COHESION_THRESHOLD = 0.15    # 2e / n(n-1) below this → "sparse"


def compute_graph_structure(
    infos: list[PageInfo],
    edges: list[tuple[int, int]],
) -> dict[str, Any] | None:
    """Run Louvain + bridge/sparse analysis on the wikilink graph.

    Returns None if networkx is missing, the graph has no edges, or it has
    fewer than _MIN_COMMUNITY_SIZE nodes (community detection meaningless).

    Result dict:
    - communities:        list[list[str]] of communities with size >= MIN
    - all_communities_count, singleton_communities: counts before filtering
    - modularity:         Q score of the partition (typically 0.3–0.7)
    - bridges:            top-K nodes by participation coefficient,
                          each {name, participation, degree, spans}
    - sparse:             communities below cohesion threshold,
                          each {members, size, cohesion, internal_edges}
    """
    try:
        import networkx as nx
        from networkx.algorithms.community import (
            louvain_communities,
            modularity as nx_modularity,
        )
    except ImportError:
        return None

    if len(infos) < _MIN_COMMUNITY_SIZE or not edges:
        return None

    # Build undirected weighted graph. build_edges already dedupes pairs,
    # so all weights start at 1.0. Multi-graph weights would aggregate
    # here naturally if we ever switch to a counted edge model.
    G = nx.Graph()
    name_of = [info.name for info in infos]
    G.add_nodes_from(name_of)
    for src_idx, tgt_idx in edges:
        G.add_edge(name_of[src_idx], name_of[tgt_idx], weight=1.0)

    # Louvain. seed fixes the partition across runs — Louvain is greedy
    # and order-sensitive, so without a seed successive snapshots could
    # show "different" structure that's actually just stochastic jitter.
    raw_comms = louvain_communities(G, weight="weight", seed=_LOUVAIN_SEED)
    comm_lists = sorted(
        [sorted(c) for c in raw_comms],
        key=lambda c: (-len(c), c[0] if c else ""),
    )
    community_of: dict[str, int] = {}
    for ci, members in enumerate(comm_lists):
        for n in members:
            community_of[n] = ci
    Q = nx_modularity(G, raw_comms, weight="weight")

    # Participation coefficient — Guimera & Amaral 2005. P=0 if all of a
    # node's neighbors live in one community (provincial node), P→1−1/k
    # if neighbors are evenly spread across k communities (bridge).
    bridges: list[dict[str, Any]] = []
    for node in G.nodes():
        deg_per_c: dict[int, float] = {}
        total = 0.0
        for nbr in G.neighbors(node):
            if nbr == node:
                continue
            c = community_of[nbr]
            w = G[node][nbr].get("weight", 1.0)
            deg_per_c[c] = deg_per_c.get(c, 0.0) + w
            total += w
        if total == 0 or len(deg_per_c) < 2:
            continue  # provincial node — not a bridge by definition
        p = 1.0 - sum((d / total) ** 2 for d in deg_per_c.values())
        bridges.append({
            "name": node,
            "participation": round(p, 3),
            "degree": int(round(total)),
            "spans": len(deg_per_c),
        })
    # Tie-break on degree (a high-P node spanning more links is more useful
    # than a high-P node with one link to each of two clusters), then name.
    bridges.sort(key=lambda b: (-b["participation"], -b["degree"], b["name"]))
    bridges = bridges[:_TOP_BRIDGES]

    # Sparse communities — internal density 2e / n(n-1). Communities of
    # size 1–2 don't have meaningful density; skip via _MIN_COMMUNITY_SIZE.
    sparse: list[dict[str, Any]] = []
    for members in comm_lists:
        n = len(members)
        if n < _MIN_COMMUNITY_SIZE:
            continue
        sub = G.subgraph(members)
        e = sub.number_of_edges()
        cohesion = 2 * e / (n * (n - 1))
        if cohesion < _SPARSE_COHESION_THRESHOLD:
            sparse.append({
                "members": list(members),
                "size": n,
                "cohesion": round(cohesion, 3),
                "internal_edges": e,
            })
    sparse.sort(key=lambda s: (s["cohesion"], -s["size"]))

    return {
        "communities": [c for c in comm_lists if len(c) >= _MIN_COMMUNITY_SIZE],
        "all_communities_count": len(comm_lists),
        "singleton_communities": sum(1 for c in comm_lists if len(c) == 1),
        "modularity": round(Q, 3),
        "bridges": bridges,
        "sparse": sparse,
    }


# ────────────────────────────────────────────────────────────────────────
# Artifact markdown page
# ────────────────────────────────────────────────────────────────────────


def _render_graph_sections(
    graph: dict[str, Any],
    *,
    graph_iframe_src: str | None = None,
    graph_html_filename: str | None = None,
) -> list[str]:
    """Render the topology block for the artifact page: optional Cytoscape
    iframe + Russian markdown sections (communities, bridges, sparse).
    Empty list if nothing meaningful to report.

    The iframe (if provided) sits at the top of the topology block so the
    visual is adjacent to its statistics, mirroring the UMAP-map / stats
    pairing earlier on the page.
    """
    out: list[str] = []
    comms = graph.get("communities") or []
    Q = graph.get("modularity", 0.0)

    out.extend([
        "",
        "## Топология wiki (Louvain)",
        "",
        "Это **второй взгляд** на структуру vault — не по эмбеддингам "
        "(семантика), а по wikilinks (как страницы реально друг друга "
        "цитируют). Алгоритм Louvain находит группы страниц с плотными "
        "связями внутри и слабыми наружу — реальные «кусты» твоей вики.",
    ])

    if graph_iframe_src is not None:
        out.extend([
            "",
            f'<iframe src="{graph_iframe_src}" '
            'style="width:100%; aspect-ratio: 4 / 3; border:1px solid #ccc; '
            'border-radius:6px; display:block;"></iframe>',
            "",
            "Force-directed раскладка (fcose): близость на экране = "
            "плотность связей. **Цвет** — primary domain (как на UMAP-карте "
            "выше). **Размер** — √(число связей). **Золотая обводка** — "
            "узлы-мосты. **Облака** — сообщества Louvain. "
            "Hover по узлу подсвечивает соседей.",
        ])
        if graph_html_filename:
            out.append("")
            out.append(
                f"Если iframe не отобразился, открой "
                f"`_attachments/{graph_html_filename}` в браузере напрямую."
            )

    out.extend([
        "",
        "**Модулярность Q** — глобальная оценка качества разбиения. "
        "Чем выше, тем чётче кусты отделены друг от друга. "
        "Типичный диапазон для содержательных графов: 0.3–0.7. "
        "Q ниже 0.3 — структура слабо выражена (мало связей или они "
        "распределены равномерно).",
        "",
        f"- Q = **{Q:.3f}**",
        f"- Сообществ всего: **{graph.get('all_communities_count', 0)}** "
        f"(из них одиночек: {graph.get('singleton_communities', 0)})",
        f"- Сообществ ≥ {_MIN_COMMUNITY_SIZE} страниц: **{len(comms)}**",
    ])

    if comms:
        out.extend([
            "",
            "| # | Размер | Страницы |",
            "|---:|---:|---|",
        ])
        for i, members in enumerate(comms, start=1):
            preview = ", ".join(f"[[{m}]]" for m in members[:5])
            if len(members) > 5:
                preview += f", … (+{len(members) - 5})"
            out.append(f"| {i} | {len(members)} | {preview} |")
        out.append("")
        out.append(
            "Сравни этот разрез с раскраской на карте по `domain:`. "
            "Совпадения подтверждают, что заявленная структура vault "
            "соответствует реальной топологии. Расхождения — самое "
            "интересное: либо домен размазан по нескольким сообществам "
            "(возможно, его пора делить), либо одно сообщество тянет "
            "страницы из разных доменов (междисциплинарная область)."
        )

    bridges = graph.get("bridges") or []
    out.extend([
        "",
        "## Узлы-мосты",
        "",
        "Страницы, чьи wikilinks ведут сразу в несколько сообществ. "
        "Метрика — **participation coefficient** $P$ "
        "(0 = все ссылки в одном сообществе, ~1 = равномерно по нескольким). "
        "Высокий $P$ + много связей = страница, держащая на себе "
        "междисциплинарные нити vault. Стоит вкладываться: улучшение бьёт "
        "по нескольким областям сразу.",
    ])
    if bridges:
        out.extend([
            "",
            "| Страница | $P$ | Связей | Сообществ |",
            "|---|---:|---:|---:|",
        ])
        for b in bridges:
            out.append(
                f"| [[{b['name']}]] | {b['participation']:.3f} | "
                f"{b['degree']} | {b['spans']} |"
            )
    else:
        out.append("")
        out.append("_Нет страниц, связывающих два или более сообществ. "
                   "Либо граф ещё мал, либо сообщества полностью изолированы._")

    sparse = graph.get("sparse") or []
    out.extend([
        "",
        "## Разреженные сообщества",
        "",
        "Сообщества, которые Louvain собрал в кластер, но внутри которых "
        "страницы почти не цитируют друг друга. Метрика — **внутренняя "
        "плотность** $\\rho = 2e / n(n-1)$ (доля реализованных рёбер от "
        f"возможных). Порог: $\\rho < {_SPARSE_COHESION_THRESHOLD}$ при "
        f"размере ≥ {_MIN_COMMUNITY_SIZE} страниц.",
        "",
        "Сигнал «область заявлена, но недокручена»: связи между этими "
        "страницами стоит явно прописать — либо это ложный кластер, и его "
        "стоит проигнорировать.",
    ])
    if sparse:
        out.extend([
            "",
            "| Размер | $\\rho$ | Внутренних рёбер | Страницы |",
            "|---:|---:|---:|---|",
        ])
        for s in sparse:
            preview = ", ".join(f"[[{m}]]" for m in s["members"][:6])
            if len(s["members"]) > 6:
                preview += f", … (+{len(s['members']) - 6})"
            out.append(
                f"| {s['size']} | {s['cohesion']:.3f} | "
                f"{s['internal_edges']} | {preview} |"
            )
    else:
        out.append("")
        out.append("_Все сообщества достаточно плотные — "
                   "разреженных не обнаружено._")

    return out


def render_artifact_page(
    stats: dict[str, Any],
    html_filename: str,
    generated_at: str,
    iframe_src: str | None = None,
    graph: dict[str, Any] | None = None,
    graph_html_filename: str | None = None,
    graph_iframe_src: str | None = None,
) -> str:
    """Render the wiki/meta/kn-maps/knowledge-map-YYYY-MM-DD.md artifact.

    Output is in Russian, uses markdown tables for stats, and embeds the
    Cytoscape HTMLs via iframes (Obsidian renders them in reading mode).
    No PNG embed — the iframes are the canonical view.

    iframe_src / graph_iframe_src are the URLs the iframes point to.
    Caller should construct them as file:// URLs against the absolute path
    of the generated HTML files; that scheme passes Obsidian's iframe
    sandbox reliably.
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
        "- **Размер** — тип страницы: domain-хабы крупнее, остальные одного "
        "размера. Семантическая карта намеренно не кодирует число связей — "
        "топологические сигналы живут на форс-графе ниже.",
        "- **Линии** — wikilinks между страницами (полупрозрачные). "
        "Видно где явные связи совпадают с семантическими, а где расходятся.",
        "- **Hover** на узле подсвечивает его 1-hop соседей и затемняет "
        "остальное — удобно для исследования окрестности страницы.",
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

    # ─── Topological view (Louvain communities + diagnostics) ─────────
    if graph is not None:
        lines.extend(_render_graph_sections(
            graph,
            graph_iframe_src=graph_iframe_src,
            graph_html_filename=graph_html_filename,
        ))

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
                    help="skip wikilink edge overlay on the UMAP map")
    ap.add_argument("--no-page", action="store_true",
                    help="skip generating wiki/meta/kn-maps/knowledge-map-*.md")
    ap.add_argument("--no-graph", action="store_true",
                    help="skip generating the Cytoscape wiki-graph HTML")
    ap.add_argument("--seed", type=int, default=42,
                    help="UMAP random_state for reproducibility (default: 42)")
    ap.add_argument("--out-dir", type=Path, default=Path("_attachments"),
                    help="output directory for HTML/PNG (default: _attachments)")
    args = ap.parse_args()

    # Lazy viz imports — let helper-only tests run without these installed.
    # numpy + umap for projection; rendering is pure-Python via wiki_graph.
    try:
        import numpy as np
        import umap
    except ImportError as e:
        print(f"ERROR: missing dependency '{e.name}'", file=sys.stderr)
        print("Run: pip3 install -r bin/requirements.txt", file=sys.stderr)
        return 2

    from wiki_graph import render_cytoscape_html

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

    # 3. UMAP. Two non-default knobs tuned to keep clusters from drifting
    # into the corners on small wiki graphs:
    # - n_neighbors=25 (default 15) → UMAP weighs more global structure,
    #   so distinct topics don't get pushed maximally apart.
    # - min_dist=0.5 (default 0.1) → points inside a cluster spread out,
    #   reducing the visual gap between clusters relative to cluster size.
    # Capped at len-1 for very small wikis.
    vecs = np.array([info.vec for info in infos_with_vecs])
    n_neighbors = min(25, len(infos_with_vecs) - 1)
    reducer = umap.UMAP(
        n_components=2,
        n_neighbors=n_neighbors,
        min_dist=0.5,
        random_state=args.seed,
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

    # 6. Output paths
    args.out_dir.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    base = f"knowledge-map-{today}"
    html_path = args.out_dir / f"{base}.html"

    # 7a. Graph topology (Louvain + bridges + sparse) — used by both views.
    graph = compute_graph_structure(infos_with_vecs, edges)
    if graph is None and edges:
        # Only warn if we *expected* topology; with --no-edges, silence is fine.
        print(
            "graph topology skipped: networkx missing or graph too small",
            file=sys.stderr,
        )

    # 7b. UMAP semantic map. Same Cytoscape engine as the force graph but
    # all topology signals (communities, bridge stars, degree-sizing) are
    # disabled — the semantic view should encode embedding similarity only.
    # Color stays domain-coded because that's a page attribute, not topology.
    positions = {
        info.name: (float(coords[i, 0]), float(coords[i, 1]))
        for i, info in enumerate(infos_with_vecs)
    }
    if args.no_edges:
        umap_edges: list[tuple[int, int]] = []
    else:
        umap_edges = edges
    umap_html = render_cytoscape_html(
        infos_with_vecs, umap_edges, graph, domain_to_color,
        page_title="Карта знаний wiki",
        subtitle="UMAP-проекция: близость = семантическая близость",
        positions=positions,
        with_communities=False,
        with_bridges=False,
        size_by_degree=False,
    )
    html_path.write_text(umap_html, encoding="utf-8")
    print(f"wrote {html_path}")

    # 7c. Force-directed topological graph (fcose layout, with communities).
    # size_by_degree=False to keep node sizes consistent with the UMAP view
    # — the topology signal here is delivered by communities, bridges, and
    # the layout itself; degree-sized nodes added visual noise on top.
    graph_html_path: Path | None = None
    if not args.no_graph and edges:
        graph_html_path = args.out_dir / f"wiki-graph-{today}.html"
        graph_html = render_cytoscape_html(
            infos_with_vecs, edges, graph, domain_to_color,
            page_title="Граф wiki — топология",
            subtitle="Force-directed (fcose): близость = плотность связей",
            size_by_degree=False,
        )
        graph_html_path.write_text(graph_html, encoding="utf-8")
        print(f"wrote {graph_html_path}")

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

        graph_iframe_src: str | None = None
        graph_html_filename: str | None = None
        if graph_html_path is not None:
            abs_graph_html = graph_html_path.resolve()
            graph_iframe_src = (
                f"file://{quote(str(abs_graph_html))}?v={cache_buster}"
            )
            graph_html_filename = graph_html_path.name

        page_md = render_artifact_page(
            stats, html_path.name, generated_at,
            iframe_src=iframe_src, graph=graph,
            graph_html_filename=graph_html_filename,
            graph_iframe_src=graph_iframe_src,
        )
        artifact_dir = WIKI_ROOT / "meta" / "kn-maps"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / f"{base}.md"
        artifact_path.write_text(page_md, encoding="utf-8")
        print(f"wrote {artifact_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
