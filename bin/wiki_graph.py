"""Cytoscape.js force-directed wiki graph artifact.

Complement to bin/knowledge_map.py. Same input data (PageInfo + edges +
graph topology), but a fundamentally different view:

    knowledge_map.py:  UMAP layout — close on screen = semantically similar
    wiki_graph.py:     fcose layout — close on screen = densely connected

The two artifacts are versioned alongside each other in _attachments/ and
both embedded as iframes into wiki/meta/kn-maps/knowledge-map-*.md.

Self-contained: vendored cytoscape.js + fcose extension are inlined into
the output HTML so it works offline and inside Obsidian's iframe sandbox
(which can block external CDN). Files live in bin/vendor/.

Single public entry point: render_cytoscape_html().
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Resolve vendor scripts relative to this file so it works regardless of
# the caller's cwd.
_VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
_VENDOR_SCRIPTS = (
    "cytoscape.min.js",   # core; defines window.cytoscape
    "layout-base.js",     # required by cose-base
    "cose-base.js",       # required by cytoscape-fcose
    "cytoscape-fcose.js", # fcose layout extension; registers via cytoscape.use()
)


def _load_vendor_scripts() -> str:
    """Concatenate vendored JS as one inline <script> body. Order matters:
    cytoscape → layout-base → cose-base → cytoscape-fcose (each later one
    expects the previous as a window global)."""
    parts: list[str] = []
    for name in _VENDOR_SCRIPTS:
        p = _VENDOR_DIR / name
        if not p.exists():
            raise FileNotFoundError(
                f"Missing vendored script: {p}. "
                "Run: curl -sSLo bin/vendor/<name> https://unpkg.com/<pkg>"
            )
        parts.append(f"/* === {name} === */\n{p.read_text(encoding='utf-8')}")
    return "\n".join(parts)


# Node sizing has two modes, picked per view:
#
# - degree-based (topology view): radius scales with sqrt(degree); domain
#   hubs get a floor so they remain visible even when sparsely linked.
#   Encodes "how connected".
# - type-based (semantic view): two fixed sizes, hubs vs the rest. Encodes
#   nothing about topology — keeps the UMAP picture about embeddings only.
_SIZE_MIN = 10
_SIZE_MAX = 32
_SIZE_DOMAIN_MIN = 20
_SIZE_TYPE_DOMAIN = 28  # type-view: domain hubs
_SIZE_TYPE_REST = 18    # type-view: ideas / entities / questions


def _node_size(
    degree: int, max_degree: int, page_type: str, *, by_degree: bool,
) -> int:
    """Map (degree, type) → display radius.

    by_degree=True  → sqrt-scaled by degree, hub floor (topology view).
    by_degree=False → fixed-by-type, two values (semantic view).
    """
    if not by_degree:
        return _SIZE_TYPE_DOMAIN if page_type == "domain" else _SIZE_TYPE_REST
    if max_degree <= 0:
        base = _SIZE_MIN
    else:
        # sqrt scaling: visual area ~ degree, not radius ~ degree.
        # Without this, the top-degree node ends up many times larger than
        # the median, which makes the rest unreadable.
        norm = (degree / max_degree) ** 0.5
        base = round(_SIZE_MIN + norm * (_SIZE_MAX - _SIZE_MIN))
    if page_type == "domain":
        base = max(base, _SIZE_DOMAIN_MIN)
    return base


def render_cytoscape_html(
    infos: list,                    # list[PageInfo] — typed to avoid circular import
    edges: list[tuple[int, int]],
    graph_structure: dict[str, Any] | None,
    domain_to_color: dict[str, str],
    *,
    page_title: str = "Граф wiki — топология",
    positions: dict[str, tuple[float, float]] | None = None,
    with_communities: bool = True,
    with_bridges: bool = True,
    size_by_degree: bool = True,
    subtitle: str | None = None,
) -> str:
    """Build a self-contained HTML page with a Cytoscape graph view.

    Two preset modes are used by knowledge_map.py:
    - Topology view (force layout): positions=None, all flags True.
      Encodes how the wiki is wired — sizes, stars, and clusters all tell
      a topology story.
    - Semantic view (UMAP-pinned): positions={name: (x, y)},
      with_communities=False, with_bridges=False, size_by_degree=False.
      Encodes embedding similarity only — no topological signals
      (clusters, mosts, degree) leak into the picture.

    Visual contract:
    - Node color: primary domain (matches knowledge_map.py palette).
    - Node size: per size_by_degree (sqrt-of-degree, or fixed-by-type).
    - Bridge nodes: star + gold halo, only when with_bridges=True.
    - Compound parents (Louvain): only when with_communities=True.
    - Labels: domain hubs always; top bridges only when with_bridges=True.
    - Hover/click: dim all but the node, its 1-hop neighbors, and edges.
    """
    # ─── Index helpers ───────────────────────────────────────────────
    name_to_idx = {info.name: i for i, info in enumerate(infos)}
    by_name = {info.name: info for info in infos}

    # Degree from undirected edge list (already deduped in build_edges)
    degree: dict[str, int] = {info.name: 0 for info in infos}
    for src, tgt in edges:
        degree[infos[src].name] += 1
        degree[infos[tgt].name] += 1
    max_deg = max(degree.values()) if degree else 0

    # ─── Communities & bridges ──────────────────────────────────────
    community_of: dict[str, int] = {}
    community_sizes: dict[int, int] = {}
    bridges: set[str] = set()
    if graph_structure is not None:
        if with_communities:
            for ci, members in enumerate(graph_structure.get("communities") or []):
                community_sizes[ci] = len(members)
                for m in members:
                    community_of[m] = ci
        if with_bridges:
            for b in graph_structure.get("bridges") or []:
                bridges.add(b["name"])

    # ─── Labels: which nodes get a visible text? ────────────────────
    # Domain hubs always; bridges only when shown (otherwise labeling
    # them would advertise a marker the user can't see).
    labeled: set[str] = {info.name for info in infos if info.page_type == "domain"}
    labeled |= bridges

    # ─── Build elements (nodes + edges + community parents) ─────────
    elements: list[dict[str, Any]] = []

    # 1. Compound parents — one per community. Skipped entirely when
    #    with_communities=False (e.g. for the UMAP semantic view, where
    #    overlaying topological clusters would muddle the reading).
    for ci, size in community_sizes.items():
        elements.append({
            "data": {
                "id": f"_community_{ci}",
                "label": f"Кластер {ci + 1} · {size}",
                "kind": "community",
            },
            "classes": "community",
        })

    # 2. Page nodes. When UMAP positions are supplied, normalize them to a
    # fixed canvas. This decouples viewport sizing from UMAP parameters —
    # whatever min_dist / n_neighbors you tune upstream, the result always
    # fits Cytoscape's default zoom range. Aspect 4:3 matches the iframe.
    _CANVAS_W = 1200.0
    _CANVAS_H = 900.0
    _norm_x = _norm_y = None
    if positions is not None and positions:
        xs = [p[0] for p in positions.values()]
        ys = [p[1] for p in positions.values()]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        x_range = (x_max - x_min) or 1.0
        y_range = (y_max - y_min) or 1.0

        def _norm_x(x: float) -> float:
            return (x - x_min) / x_range * _CANVAS_W

        def _norm_y(y: float) -> float:
            # Flip vertical axis so positive y is up (matches the math
            # convention; Cytoscape's screen y points down).
            return (y_max - y) / y_range * _CANVAS_H
    for info in infos:
        primary = info.domains[0] if info.domains else None
        color = domain_to_color.get(primary, "#6B7280") if primary else "#6B7280"
        deg = degree[info.name]
        size = _node_size(deg, max_deg, info.page_type, by_degree=size_by_degree)

        cls_parts = ["node"]
        if info.page_type == "domain":
            cls_parts.append("domain-hub")
        if info.name in bridges:
            cls_parts.append("bridge")
        if info.name in labeled:
            cls_parts.append("labeled")

        node_data: dict[str, Any] = {
            "id": info.name,
            "label": info.name,
            "color": color,
            "size": size,
            "page_type": info.page_type,
            "degree": deg,
            "domains": info.domains,
        }
        ci = community_of.get(info.name)
        if ci is not None:
            node_data["parent"] = f"_community_{ci}"

        node_element: dict[str, Any] = {
            "data": node_data,
            "classes": " ".join(cls_parts),
        }
        if positions is not None and info.name in positions and _norm_x is not None:
            x, y = positions[info.name]
            node_element["position"] = {
                "x": _norm_x(float(x)),
                "y": _norm_y(float(y)),
            }

        elements.append(node_element)

    # 3. Edges — undirected, already deduped
    for src, tgt in edges:
        s_name = infos[src].name
        t_name = infos[tgt].name
        elements.append({
            "data": {
                "id": f"e_{src}_{tgt}",
                "source": s_name,
                "target": t_name,
            },
            "classes": "edge",
        })

    # ─── Statistics for the header strip ────────────────────────────
    n_nodes = len(infos)
    n_edges = len(edges)
    n_communities = len(community_sizes)
    n_bridges = len(bridges)
    Q = (graph_structure or {}).get("modularity")

    header_stats = (
        f"{n_nodes} страниц · {n_edges} связей · "
        f"{n_communities} сообществ"
        + (f" · Q = {Q:.3f}" if Q is not None else "")
        + (f" · {n_bridges} мостов" if n_bridges else "")
    )

    # ─── Legend: only describe glyphs the view actually shows ───────
    legend_rows: list[str] = []
    if with_bridges:
        legend_rows.append(
            '<div class="row"><span class="swatch star">&#9733;</span> '
            "узел-мост</div>"
        )
    legend_rows.append(
        '<div class="row"><span class="swatch hub"></span> домен-хаб</div>'
    )
    size_text = (
        "размер = √(связей)" if size_by_degree else "размер = тип страницы"
    )
    legend_rows.append(
        '<div class="row" style="margin-top:6px; color:#9CA3AF;">'
        f"Цвет = primary domain · {size_text}</div>"
    )
    if with_communities:
        legend_rows.append(
            '<div class="row" style="color:#9CA3AF;">'
            "Облако = сообщество (Louvain)</div>"
        )
    legend_html = "\n".join(legend_rows)

    # ─── Layout config: preset (UMAP) vs fcose (force-directed) ─────
    if positions is not None:
        # preset: pinned coordinates already on the elements; cy.fit() at
        # the end re-centers and zooms to fit the viewport.
        layout_config = {"name": "preset", "fit": True, "padding": 40}
    else:
        layout_config = {
            "name": "fcose",
            "quality": "proof",
            "randomize": True,
            "animate": False,
            "nodeRepulsion": 8000,
            "idealEdgeLength": 90,
            "edgeElasticity": 0.3,
            "gravity": 0.25,
            "gravityRangeCompound": 1.5,
            "nestingFactor": 0.6,
            "numIter": 2500,
            "tile": True,
            "uniformNodeDimensions": False,
            "packComponents": True,
        }

    # ─── Inline JS deps + render template ───────────────────────────
    vendor_js = _load_vendor_scripts()
    elements_json = json.dumps(elements, ensure_ascii=False)
    layout_json = json.dumps(layout_config)

    subtitle_html = (
        f'<div class="subtitle">{subtitle}</div>' if subtitle else ""
    )

    # Note: the JS below is intentionally vanilla (no bundling step). It
    # reads `window.__WIKI_GRAPH_DATA__` set inline below the script tag,
    # so the data and the bootstrap can be swapped independently.
    return _HTML_TEMPLATE.format(
        title=page_title,
        header_stats=header_stats,
        subtitle_html=subtitle_html,
        legend_html=legend_html,
        vendor_js=vendor_js,
        elements_json=elements_json,
        layout_json=layout_json,
    )


# ────────────────────────────────────────────────────────────────────────
# HTML template
# ────────────────────────────────────────────────────────────────────────
#
# Single self-contained file. Dark dashboard styling: #0F1419 background,
# Inter font, light-gray text. Layout (preset vs fcose) is supplied by
# the caller via {layout_json}; node positions are otherwise static
# unless the user drags them.
#
# Why .format() and not f-string: the JS body contains literal { } that
# would conflict with f-string interpolation. .format() lets us escape
# them with {{ }} only where needed.

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  html, body {{
    margin: 0; padding: 0; height: 100%;
    background: #0F1419; color: #E5E7EB;
    font-family: Inter, -apple-system, BlinkMacSystemFont, sans-serif;
    overflow: hidden;
  }}
  #header {{
    position: absolute; top: 0; left: 0; right: 0;
    padding: 12px 18px; z-index: 10;
    display: flex; justify-content: space-between; align-items: center;
    pointer-events: none;
  }}
  #header .title-block {{
    display: flex; flex-direction: column; gap: 2px;
  }}
  #header h1 {{
    margin: 0; font-size: 16px; font-weight: 600; color: #E5E7EB;
  }}
  #header .subtitle {{
    font-size: 11px; color: #9CA3AF; font-weight: 400;
  }}
  #header .stats {{
    font-size: 12px; color: #9CA3AF;
  }}
  #cy {{
    position: absolute; top: 0; left: 0; right: 0; bottom: 0;
  }}
  #legend {{
    position: absolute; bottom: 12px; left: 12px; z-index: 10;
    background: rgba(15, 20, 25, 0.85);
    border: 1px solid #1F2937; border-radius: 6px;
    padding: 10px 12px; font-size: 11px; color: #E5E7EB;
    pointer-events: none; max-width: 220px;
  }}
  #legend .row {{
    display: flex; align-items: center; gap: 6px; margin: 3px 0;
  }}
  #legend .swatch {{
    display: inline-flex; align-items: center; justify-content: center;
    width: 14px; height: 14px;
  }}
  #legend .swatch.dot {{
    width: 10px; height: 10px; border-radius: 50%;
    background: #4B5563;
  }}
  #legend .swatch.hub {{
    width: 12px; height: 12px; border-radius: 50%;
    background: #4B5563; border: 2px solid #F9FAFB;
  }}
  #legend .swatch.star {{
    color: #FBBF24; font-size: 16px; line-height: 1;
    text-shadow: 0 0 6px rgba(251, 191, 36, 0.85);
  }}
  #tooltip {{
    position: absolute; z-index: 20;
    background: rgba(15, 20, 25, 0.95);
    border: 1px solid #374151; border-radius: 6px;
    padding: 8px 10px; font-size: 12px; color: #E5E7EB;
    pointer-events: none; display: none; max-width: 280px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
  }}
  #tooltip .name {{
    font-weight: 600; margin-bottom: 4px;
  }}
  #tooltip .meta {{
    color: #9CA3AF; font-size: 11px;
  }}
</style>
</head>
<body>
<div id="header">
  <div class="title-block">
    <h1>{title}</h1>
    {subtitle_html}
  </div>
  <div class="stats">{header_stats}</div>
</div>
<div id="cy"></div>
<div id="legend">
{legend_html}
</div>
<div id="tooltip"></div>

<script>
{vendor_js}
</script>

<script>
window.__WIKI_GRAPH_DATA__ = {elements_json};
</script>

<script>
(function () {{
  // Register the fcose extension. The vendored UMD bundles attach to
  // window globals; cytoscape.use() takes the registrar function.
  if (window.cytoscape && window.cytoscapeFcose) {{
    window.cytoscape.use(window.cytoscapeFcose);
  }}

  var cy = window.cytoscape({{
    container: document.getElementById("cy"),
    elements: window.__WIKI_GRAPH_DATA__,
    minZoom: 0.2,
    maxZoom: 4,
    wheelSensitivity: 0.2,
    style: [
      // Default node — color/size from data, dark outline.
      {{
        selector: "node",
        style: {{
          "background-color": "data(color)",
          "width": "data(size)",
          "height": "data(size)",
          "border-width": 1,
          "border-color": "#1F2937",
          "color": "#E5E7EB",
          "font-family": "Inter, sans-serif",
          "font-size": 11,
          "text-valign": "bottom",
          "text-margin-y": 4,
          "text-outline-color": "#0F1419",
          "text-outline-width": 2,
          "label": "",
          "z-index": 10
        }}
      }},
      // Labeled nodes (domain hubs + bridges) — show name underneath.
      {{
        selector: "node.labeled",
        style: {{
          "label": "data(label)",
          "font-size": 12,
          "font-weight": 600
        }}
      }},
      // Domain hubs — thicker light outline so the hub role reads even
      // when the node is small.
      {{
        selector: "node.domain-hub",
        style: {{
          "border-width": 2,
          "border-color": "#F9FAFB",
          "font-size": 13
        }}
      }},
      // Bridge nodes — distinct shape (star) so they pop without color
      // conflict against domain-hubs' white border. Slight gold halo
      // via shadow reinforces the "important hub" reading.
      {{
        selector: "node.bridge",
        style: {{
          "shape": "star",
          "border-width": 1,
          "border-color": "#FBBF24",
          "shadow-blur": 14,
          "shadow-color": "#FBBF24",
          "shadow-opacity": 0.85,
          "shadow-offset-x": 0,
          "shadow-offset-y": 0
        }}
      }},
      // Compound parents (communities) — translucent rounded rect.
      {{
        selector: "node.community",
        style: {{
          "background-color": "#1F2937",
          "background-opacity": 0.25,
          "border-width": 1,
          "border-color": "#374151",
          "border-style": "dashed",
          "shape": "round-rectangle",
          "padding": 18,
          "label": "data(label)",
          "color": "#9CA3AF",
          "font-size": 11,
          "font-style": "italic",
          "text-valign": "top",
          "text-halign": "center",
          "text-margin-y": -4,
          "z-index": 0,
          "events": "no"
        }}
      }},
      // Edges — thin, translucent indigo.
      {{
        selector: "edge",
        style: {{
          "width": 1,
          "line-color": "rgba(129, 140, 248, 0.25)",
          "curve-style": "straight",
          "z-index": 1
        }}
      }},
      // Highlight states — set on hover.
      {{
        selector: ".faded",
        style: {{
          "opacity": 0.12
        }}
      }},
      {{
        selector: ".highlight-edge",
        style: {{
          "line-color": "rgba(129, 140, 248, 0.85)",
          "width": 2,
          "z-index": 5
        }}
      }},
      {{
        selector: ".highlight-node",
        style: {{
          "border-color": "#A5B4FC",
          "border-width": 3,
          "z-index": 20
        }}
      }}
    ],
    // Layout chosen by Python: 'preset' for UMAP-pinned coords, 'fcose'
    // for force-directed topology view. See render_cytoscape_html().
    layout: {layout_json}
  }});

  // Hover highlight: dim everything except the focused node,
  // its 1-hop neighbors, and incident edges.
  var tooltip = document.getElementById("tooltip");

  function highlight(node) {{
    var neighborhood = node.closedNeighborhood();
    cy.elements().not(neighborhood).addClass("faded");
    neighborhood.nodes().not(node).addClass("highlight-node");
    neighborhood.edges().addClass("highlight-edge");
    node.addClass("highlight-node");
  }}

  function clearHighlight() {{
    cy.elements().removeClass("faded highlight-node highlight-edge");
  }}

  cy.on("mouseover", "node", function (evt) {{
    var n = evt.target;
    if (n.hasClass("community")) return;
    highlight(n);

    var d = n.data();
    var domains = (d.domains || []).map(function (x) {{ return x; }}).join(", ") || "—";
    tooltip.innerHTML =
      '<div class="name">' + d.label + '</div>' +
      '<div class="meta">тип: ' + (d.page_type || "—") + '</div>' +
      '<div class="meta">домены: ' + domains + '</div>' +
      '<div class="meta">связей: ' + d.degree + '</div>';
    tooltip.style.display = "block";
  }});

  cy.on("mousemove", "node", function (evt) {{
    if (evt.target.hasClass("community")) return;
    var pos = evt.renderedPosition || evt.cyRenderedPosition || {{ x: 0, y: 0 }};
    tooltip.style.left = (pos.x + 16) + "px";
    tooltip.style.top = (pos.y + 16) + "px";
  }});

  cy.on("mouseout", "node", function () {{
    clearHighlight();
    tooltip.style.display = "none";
  }});

  cy.on("tap", function (evt) {{
    if (evt.target === cy) {{
      clearHighlight();
      tooltip.style.display = "none";
    }}
  }});
}})();
</script>
</body>
</html>
"""
