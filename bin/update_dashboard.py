#!/usr/bin/env python3
"""update_dashboard.py — light-touch dashboard refresh.

Recomputes all non-UMAP metrics (counts, wikilinks, Louvain communities,
similarity percentiles, distributions, word cloud), appends or updates a
record in history.jsonl, then rewrites wiki/meta/vault-explorer.html
with the new data inline.

Heavy artifacts (UMAP, force-graph rendering, LLM insights, sankey/
treemap data) are owned by bin/knowledge_map.py and run on a separate
schedule. This script preserves their last values when present.

Typical usage:
    python3 bin/update_dashboard.py                  # default trigger
    python3 bin/update_dashboard.py --trigger /ingest

Exit codes:
    0 — dashboard updated
    1 — nothing to update (no wiki pages)
    2 — internal error
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

# Reuse from sibling scripts.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from static_lint import discover_pages, Page  # type: ignore


# ────────────────────────────────────────────────────────────────────────
# Paths
# ────────────────────────────────────────────────────────────────────────

WIKI_ROOT = Path("wiki")
META_DIR = WIKI_ROOT / "meta"
HISTORY_PATH = META_DIR / "snapshots" / "history.jsonl"
TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "vault-explorer.html.tpl"
OUTPUT_HTML = META_DIR / "vault-explorer.html"
ATTACHMENTS_DIR = Path("_attachments")
EMBEDDINGS_PATH = META_DIR / "embeddings.json"

# Vendor JS (committed in bin/vendor/) is mirrored to wiki/meta/vendor/ so
# vault-explorer.html can reference it via the simple relative path `vendor/*`.
BIN_VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
META_VENDOR_DIR = META_DIR / "vendor"
VENDOR_JS_FILES = (
    "chart.umd.min.js",
    "chartjs-chart-sankey.min.js",
    "chartjs-chart-treemap.min.js",
)

VAULT_NAME = "llm-wiki"   # override via env or repo config later if needed

HISTORY_RETENTION_ENTRIES = 365  # keep at most a year of records


# ────────────────────────────────────────────────────────────────────────
# Wikilink graph
# ────────────────────────────────────────────────────────────────────────

_WIKILINK_RE = re.compile(r"(?<!\!)\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


def normalize_wikilink_target(raw: str) -> str:
    """`wiki/ideas/X` or `X` → `X` (basename without extension)."""
    t = raw.strip()
    if "/" in t:
        t = t.rsplit("/", 1)[-1]
    if t.endswith(".md"):
        t = t[:-3]
    return t


def extract_body_wikilinks(body: str) -> list[str]:
    """Find [[X]] in markdown body, ignoring fenced code and inline code."""
    stripped = _CODE_FENCE_RE.sub("", body)
    stripped = _INLINE_CODE_RE.sub("", stripped)
    return [normalize_wikilink_target(m.group(1)) for m in _WIKILINK_RE.finditer(stripped)]


def extract_frontmatter_wikilinks(page: Page) -> list[str]:
    """Pull wikilinks out of frontmatter list-fields (related, domain, sources)."""
    if page.fm is None:
        return []
    out: list[str] = []
    for field in ("related", "domain", "sources"):
        v = page.fm.fields.get(field)
        if isinstance(v, list):
            for item in v:
                if not isinstance(item, str):
                    continue
                m = _WIKILINK_RE.search(item)
                if m:
                    out.append(normalize_wikilink_target(m.group(1)))
                else:
                    out.append(normalize_wikilink_target(item))
    return out


def build_wikilink_graph(pages: list[Page]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Return (outgoing, incoming) edge maps keyed by page basename.

    Edges only count if target also has a page (red-links ignored).
    """
    page_names = {p.name for p in pages}
    outgoing: dict[str, set[str]] = defaultdict(set)
    incoming: dict[str, set[str]] = defaultdict(set)
    for p in pages:
        body_targets = extract_body_wikilinks(p.body)
        fm_targets = extract_frontmatter_wikilinks(p)
        for tgt in set(body_targets + fm_targets):
            if tgt == p.name or tgt not in page_names:
                continue
            outgoing[p.name].add(tgt)
            incoming[tgt].add(p.name)
    return outgoing, incoming


# ────────────────────────────────────────────────────────────────────────
# Embeddings & similarity
# ────────────────────────────────────────────────────────────────────────


def load_embeddings() -> dict[str, list[float]]:
    """basename → vector. Returns empty dict if embeddings missing."""
    if not EMBEDDINGS_PATH.exists():
        return {}
    raw = json.loads(EMBEDDINGS_PATH.read_text(encoding="utf-8"))
    items = raw.get("items") or raw  # support both wrapper and flat formats
    out: dict[str, list[float]] = {}
    if isinstance(items, dict):
        for k, v in items.items():
            if isinstance(v, dict) and "vec" in v:
                out[Path(k).stem] = v["vec"]
            elif isinstance(v, list):
                out[Path(k).stem] = v
    elif isinstance(items, list):
        for entry in items:
            if not isinstance(entry, dict):
                continue
            key = entry.get("path") or entry.get("key") or entry.get("name")
            vec = entry.get("vec") or entry.get("vector")
            if key and vec:
                out[Path(key).stem] = vec
    return out


def cos_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def pairwise_similarity(embeddings: dict[str, list[float]]) -> tuple[list[float], tuple[str, str, float] | None]:
    """Return (all_sims_sorted, tightest_pair). Tightest = (a, b, sim)."""
    keys = sorted(embeddings.keys())
    if len(keys) < 2:
        return [], None
    sims: list[float] = []
    tightest: tuple[str, str, float] | None = None
    for i, a in enumerate(keys):
        va = embeddings[a]
        for b in keys[i + 1:]:
            s = cos_sim(va, embeddings[b])
            sims.append(s)
            if tightest is None or s > tightest[2]:
                tightest = (a, b, s)
    sims.sort()
    return sims, tightest


def percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * (p / 100)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def most_isolated_page(embeddings: dict[str, list[float]]) -> tuple[str, float] | None:
    """Page with the lowest *max* similarity to any other — semantically uniquest."""
    keys = sorted(embeddings.keys())
    if len(keys) < 2:
        return None
    best: tuple[str, float] | None = None  # min(max_sim)
    for k in keys:
        vk = embeddings[k]
        max_sim = 0.0
        for o in keys:
            if o == k:
                continue
            s = cos_sim(vk, embeddings[o])
            if s > max_sim:
                max_sim = s
        if best is None or max_sim < best[1]:
            best = (k, max_sim)
    return best


def domain_cohesion(pages: list[Page], embeddings: dict[str, list[float]]) -> dict[str, float]:
    """Avg internal cosine per primary domain."""
    by_domain: dict[str, list[str]] = defaultdict(list)
    for p in pages:
        if p.fm is None:
            continue
        ds = p.fm.fields.get("domain") or []
        if not isinstance(ds, list) or not ds:
            continue
        # primary domain = first entry, strip [[ ]]
        primary = ds[0]
        m = _WIKILINK_RE.search(primary) if isinstance(primary, str) else None
        name = normalize_wikilink_target(m.group(1)) if m else (primary if isinstance(primary, str) else "")
        if not name:
            continue
        if p.name in embeddings:
            by_domain[name].append(p.name)

    out: dict[str, float] = {}
    for dom, members in by_domain.items():
        if len(members) < 2:
            out[dom] = 0.0
            continue
        sims = []
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                sims.append(cos_sim(embeddings[a], embeddings[b]))
        out[dom] = sum(sims) / len(sims) if sims else 0.0
    return out


# ────────────────────────────────────────────────────────────────────────
# Topology — Louvain communities + bridges (best-effort, needs networkx)
# ────────────────────────────────────────────────────────────────────────


def compute_topology(outgoing: dict[str, set[str]], incoming: dict[str, set[str]]) -> dict[str, Any]:
    """Run Louvain on undirected wikilink graph. Best-effort: returns zeros
    if networkx missing or graph too small."""
    try:
        import networkx as nx
    except ImportError:
        return {"Q": None, "communities": 0, "bridges": 0, "communities_list": []}

    G = nx.Graph()
    nodes = set(outgoing) | set(incoming)
    for n in nodes:
        G.add_node(n)
    for src, tgts in outgoing.items():
        for t in tgts:
            G.add_edge(src, t)

    if G.number_of_edges() < 2 or G.number_of_nodes() < 4:
        return {"Q": None, "communities": 0, "bridges": 0, "communities_list": []}

    try:
        from networkx.algorithms.community import louvain_communities, modularity
        comms = louvain_communities(G, seed=42)
        Q = modularity(G, comms)
    except Exception:
        return {"Q": None, "communities": 0, "bridges": 0, "communities_list": []}

    big_comms = [list(c) for c in comms if len(c) >= 3]

    # Bridges via participation coefficient P > 0.3
    node_to_comm: dict[str, int] = {}
    for idx, c in enumerate(comms):
        for n in c:
            node_to_comm[n] = idx

    bridges: list[dict[str, Any]] = []
    for n in G.nodes():
        if G.degree(n) == 0:
            continue
        comm_counts: dict[int, int] = defaultdict(int)
        for nb in G.neighbors(n):
            comm_counts[node_to_comm.get(nb, -1)] += 1
        total = sum(comm_counts.values())
        if total == 0:
            continue
        P = 1 - sum((c / total) ** 2 for c in comm_counts.values())
        if P > 0.3:
            bridges.append({
                "name": n,
                "participation": round(P, 3),
                "degree": G.degree(n),
                "spans": len(comm_counts),
            })
    bridges.sort(key=lambda b: (-b["participation"], -b["degree"]))

    return {
        "Q": round(Q, 4),
        "communities": len(big_comms),
        "bridges": len(bridges),
        "top_bridge": bridges[0] if bridges else None,
        "communities_list": big_comms,
    }


# ────────────────────────────────────────────────────────────────────────
# Word cloud — tokenize + stop-list + frequency
# ────────────────────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9_-]{2,}")
# Frontmatter, code blocks, inline code, wikilinks are stripped before tokenize.

STOPWORDS = {
    # Russian
    "это", "так", "что", "как", "для", "при", "или", "его", "ему", "её", "они",
    "над", "под", "над", "без", "под", "при", "вид", "если", "когда", "уже",
    "также", "тоже", "там", "тут", "тогда", "потому", "поэтому", "только",
    "может", "должен", "нужно", "надо", "быть", "есть", "был", "была", "было",
    "будет", "будут", "очень", "ещё", "еще", "там", "тут", "где", "куда",
    "откуда", "почему", "чтобы", "лишь", "хотя", "однако", "между", "вместо",
    "около", "вокруг", "через", "против", "после", "перед", "сразу", "потом",
    "затем", "наконец", "сначала", "сейчас", "теперь", "сегодня", "вчера",
    "завтра", "всего", "всех", "всем", "всё", "все", "себя", "своё", "свои",
    "своя", "свой", "наш", "ваш", "мне", "тебе", "мной", "тобой", "ним",
    "ней", "них", "нём", "нём", "нам", "вам", "что-то", "кого", "кому",
    # English
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "had",
    "her", "was", "one", "our", "out", "day", "get", "has", "him", "his",
    "how", "man", "new", "now", "old", "see", "two", "way", "who", "boy",
    "did", "its", "let", "put", "say", "she", "too", "use", "with", "this",
    "that", "from", "they", "have", "been", "were", "what", "when", "where",
    "which", "while", "their", "there", "would", "could", "should", "into",
    "than", "then", "them", "these", "those", "such", "some", "more", "most",
    "very", "well", "also", "just", "like", "even", "only", "much", "many",
    "any", "every", "each", "both", "few", "other", "another", "same", "own",
}


def extract_text_for_wordcloud(pages: list[Page]) -> str:
    """Concatenate cleaned body texts (no frontmatter, no code, no wikilinks)."""
    parts: list[str] = []
    for p in pages:
        body = p.body
        body = _CODE_FENCE_RE.sub(" ", body)
        body = _INLINE_CODE_RE.sub(" ", body)
        body = _WIKILINK_RE.sub(" ", body)
        body = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", body)   # images
        body = re.sub(r"\[[^\]]*\]\([^)]*\)", " ", body)    # links
        parts.append(body)
    return "\n".join(parts)


def compute_wordcloud(pages: list[Page], top_n: int = 60) -> list[tuple[str, int]]:
    text = extract_text_for_wordcloud(pages)
    tokens = _TOKEN_RE.findall(text)
    counts: Counter[str] = Counter()
    for t in tokens:
        # Preserve case for short acronyms (RLHF, PPO) but lowercase common words.
        if len(t) <= 5 and t.isupper():
            key = t
        else:
            key = t.lower()
        if key.lower() in STOPWORDS:
            continue
        if len(key) < 3:
            continue
        counts[key] += 1
    return counts.most_common(top_n)


# ────────────────────────────────────────────────────────────────────────
# Distributions: type, domain, provenance, status
# ────────────────────────────────────────────────────────────────────────

CONTENT_TYPES = ("idea", "entity", "question", "domain", "mind")


def get_primary_domain(page: Page) -> str | None:
    if page.fm is None:
        return None
    ds = page.fm.fields.get("domain") or []
    if not isinstance(ds, list) or not ds:
        return None
    first = ds[0]
    if not isinstance(first, str):
        return None
    m = _WIKILINK_RE.search(first)
    return normalize_wikilink_target(m.group(1)) if m else first.strip()


def compute_distributions(pages: list[Page]) -> dict[str, Any]:
    type_counts: dict[str, int] = {t: 0 for t in CONTENT_TYPES}
    domain_counts: Counter[str] = Counter()
    provenance: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    unassigned = 0
    for p in pages:
        # Only count content pages (skip pages without frontmatter or with meta type)
        if p.fm is None:
            continue
        ptype = p.fm.fields.get("type")
        if ptype not in CONTENT_TYPES:
            continue
        type_counts[ptype] = type_counts.get(ptype, 0) + 1
        dom = get_primary_domain(p)
        if dom:
            domain_counts[dom] += 1
        else:
            unassigned += 1
        prov = p.fm.fields.get("provenance") or "manual"
        if isinstance(prov, str):
            provenance[prov] += 1
        st = p.fm.fields.get("status")
        if isinstance(st, str):
            statuses[st] += 1
    return {
        "types": type_counts,
        "domains": dict(domain_counts),
        "provenance": dict(provenance),
        "statuses": dict(statuses),
        "unassigned": unassigned,
    }


# ────────────────────────────────────────────────────────────────────────
# Health score (deterministic, no LLM)
# ────────────────────────────────────────────────────────────────────────


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def compute_health(metrics: dict[str, Any]) -> dict[str, Any]:
    pages = metrics["pages_total"]
    if pages == 0:
        return {"total": 0, "rows": []}
    orphans = metrics["orphans"]
    density = metrics["wikilinks"] / pages
    Q = metrics.get("Q") or 0.0
    statuses = metrics.get("statuses_dist", {})
    ready_ratio = statuses.get("ready", 0) / pages
    unassigned = metrics.get("unassigned", 0)
    content_total = sum(metrics["types_dist"].values())
    domain_ratio = (content_total - unassigned) / content_total if content_total else 0.0

    orphan_score = clamp(100 - (orphans / pages) * 1000, 0, 100)
    density_score = clamp(100 - abs(density - 7) * 14, 0, 100)
    Q_score = clamp((Q / 0.7) * 100, 0, 100) if Q else 50
    ready_score = clamp(ready_ratio * 100, 0, 100)
    domain_score = clamp(domain_ratio * 100, 0, 100)

    total = round(
        orphan_score * 0.25 + density_score * 0.15 + Q_score * 0.25 +
        ready_score * 0.20 + domain_score * 0.15
    )
    return {
        "total": total,
        "rows": [
            {"key": "orphans",  "label": "Сироты",       "score": round(orphan_score),  "detail": f"{orphans} / {pages}"},
            {"key": "density",  "label": "Плотность",    "score": round(density_score), "detail": f"{density:.2f}"},
            {"key": "modular",  "label": "Q (Louvain)",  "score": round(Q_score),       "detail": f"{Q:.3f}" if Q else "—"},
            {"key": "ready",    "label": "Зрелых",       "score": round(ready_score),   "detail": f"{round(ready_ratio * 100)}%"},
            {"key": "domain",   "label": "Классифицир.", "score": round(domain_score),  "detail": f"{round(domain_ratio * 100)}%"},
        ],
    }


# ────────────────────────────────────────────────────────────────────────
# History
# ────────────────────────────────────────────────────────────────────────


def load_history() -> list[dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in HISTORY_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def save_history(history: list[dict[str, Any]]) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(h, ensure_ascii=False) for h in history)
    HISTORY_PATH.write_text(payload + "\n", encoding="utf-8")


def build_history_entry(metrics: dict[str, Any], trigger: str, prev: dict[str, Any] | None) -> dict[str, Any]:
    now_iso = dt.datetime.now().isoformat(timespec="minutes")
    delta = None
    if prev is not None:
        prev_pages = set(prev.get("pages_list", []))
        cur_pages = set(metrics["pages_list"])
        pages_added = sorted(cur_pages - prev_pages)
        pages_removed = sorted(prev_pages - cur_pages)
        delta = {
            "pages_added": pages_added,
            "pages_removed": pages_removed,
            "wikilinks": metrics["wikilinks"] - prev.get("wikilinks", metrics["wikilinks"]),
            "orphans": metrics["orphans"] - prev.get("orphans", metrics["orphans"]),
            "Q": (metrics.get("Q") - prev.get("Q")) if metrics.get("Q") is not None and prev.get("Q") is not None else None,
        }
    return {
        "ts": now_iso,
        "trigger": trigger,
        "pages": metrics["pages_total"],
        "wikilinks": metrics["wikilinks"],
        "orphans": metrics["orphans"],
        "Q": metrics.get("Q"),
        "communities": metrics.get("communities") or 0,
        "bridges": metrics.get("bridges") or 0,
        "sim_p50": metrics.get("sim_p50"),
        "sim_p75": metrics.get("sim_p75"),
        "sim_p95": metrics.get("sim_p95"),
        "types": metrics["types_dist"],
        "domains": metrics["domains_dist"],
        "cohesion": metrics.get("cohesion", {}),
        "delta": delta,
        "pages_list": metrics["pages_list"],   # used for next-turn diff
    }


def update_history(history: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Append entry, but if entry already exists for the same minute,
    overwrite the last one (avoids history bloat for rapid stop-hooks)."""
    if history and history[-1]["ts"] == entry["ts"]:
        history[-1] = entry
    else:
        history.append(entry)
    # Retain the last N entries
    if len(history) > HISTORY_RETENTION_ENTRIES:
        history = history[-HISTORY_RETENTION_ENTRIES:]
    return history


# ────────────────────────────────────────────────────────────────────────
# Latest snapshot detection
# ────────────────────────────────────────────────────────────────────────

_SNAP_RE = re.compile(r"^(?:snapshot|knowledge-map)-(\d{4}-\d{2}-\d{2})\.html$")
_GRAPH_RE = re.compile(r"^(?:snapshot-graph|wiki-graph)-(\d{4}-\d{2}-\d{2})\.html$")


def find_latest_snapshot() -> dict[str, Any]:
    """Locate the most recent dated map HTMLs in _attachments/.

    Prefers new 'snapshot-*' names but falls back to legacy 'knowledge-map-*'
    so historical files stay usable.
    """
    if not ATTACHMENTS_DIR.is_dir():
        return {"date": None, "umap_iframe": None, "graph_iframe": None}

    def find_latest(pattern: re.Pattern, prefer_new: str, fallback: str) -> tuple[str | None, str | None]:
        candidates: list[tuple[str, str]] = []  # (date, basename)
        for f in ATTACHMENTS_DIR.iterdir():
            m = pattern.match(f.name)
            if m:
                candidates.append((m.group(1), f.name))
        if not candidates:
            return None, None
        candidates.sort(reverse=True)
        date, name = candidates[0]
        return date, f"../../_attachments/{name}"

    umap_date, umap_path = find_latest(_SNAP_RE, "snapshot", "knowledge-map")
    graph_date, graph_path = find_latest(_GRAPH_RE, "snapshot-graph", "wiki-graph")
    return {
        "date": umap_date,
        "umap_iframe": umap_path,
        "graph_iframe": graph_path,
    }


# ────────────────────────────────────────────────────────────────────────
# Build dashboard data
# ────────────────────────────────────────────────────────────────────────


def fmt_delta(cur: float | int, prev: float | int | None, *, signed: bool = True, fmt: str = "{:+d}", suffix: str = " за 24ч") -> tuple[str, str]:
    """Return (text, css_class)."""
    if prev is None:
        return ("без изменений", "")
    diff = cur - prev
    if diff == 0:
        return ("без изменений", "")
    cls = "up" if diff > 0 else "down"
    return (fmt.format(diff) + suffix, cls)


def aggregate_24h(history: list[dict[str, Any]], now: dt.datetime) -> dict[str, Any]:
    """Sum deltas in the last 24h."""
    cutoff = now - dt.timedelta(hours=24)
    within = [h for h in history if dt.datetime.fromisoformat(h["ts"]) > cutoff and h.get("delta")]
    pages_added: list[str] = []
    wikilinks = 0
    orphans = 0
    Q_sum = 0.0
    Q_present = False
    for h in within:
        d = h["delta"]
        pages_added.extend(d.get("pages_added", []))
        wikilinks += d.get("wikilinks") or 0
        orphans += d.get("orphans") or 0
        if d.get("Q") is not None:
            Q_sum += d["Q"]
            Q_present = True
    return {
        "pages_added": pages_added,
        "wikilinks": wikilinks,
        "orphans": orphans,
        "Q": Q_sum if Q_present else None,
    }


def build_cards(metrics: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    now = dt.datetime.now()
    agg = aggregate_24h(history, now)
    pages = metrics["pages_total"]

    # 24h deltas straight from aggregate
    def delta_24h(value: int | float, target_key: str) -> tuple[str, str]:
        if target_key in ("pages_added",):
            n = len(agg.get(target_key) or [])
        else:
            n = agg.get(target_key) or 0
        if n == 0:
            return ("без изменений", "")
        sign = "+" if n > 0 else ""
        cls = "up" if (n > 0 and target_key != "orphans") else ("down" if (n > 0 and target_key == "orphans") else ("up" if n < 0 and target_key == "orphans" else "down"))
        suffix = "" if target_key == "orphans" and n > 2 else ""
        warn_mark = " ⚠" if target_key == "orphans" and n > 2 else ""
        return (f"{sign}{n} за 24ч{warn_mark}", cls)

    pages_delta = delta_24h(pages, "pages_added")
    links_delta = delta_24h(metrics["wikilinks"], "wikilinks")
    orphans_delta = delta_24h(metrics["orphans"], "orphans")

    topology = metrics.get("topology", {})
    top_bridge = topology.get("top_bridge")
    Q = metrics.get("Q")
    prev_Q = history[-2]["Q"] if len(history) >= 2 and history[-2].get("Q") is not None else None
    Q_delta = ""
    Q_delta_cls = ""
    if prev_Q is not None and Q is not None:
        diff = Q - prev_Q
        if abs(diff) >= 0.001:
            Q_delta = f"{diff:+.3f} за 24ч"
            Q_delta_cls = "up" if diff > 0 else "down"
    if not Q_delta:
        Q_delta = "без изменений"

    density = (metrics["wikilinks"] / pages) if pages else 0.0
    prev_density = (history[-2]["wikilinks"] / history[-2]["pages"]) if len(history) >= 2 and history[-2].get("pages") else None
    density_delta = ""
    density_delta_cls = ""
    if prev_density is not None and abs(density - prev_density) >= 0.005:
        diff = density - prev_density
        density_delta = f"{diff:+.2f} за 24ч"
        density_delta_cls = "up" if diff > 0 else "down"
    else:
        density_delta = "без изменений"

    sim_p50 = metrics.get("sim_p50")
    sim_p75 = metrics.get("sim_p75")
    sim_p95 = metrics.get("sim_p95")

    def sim_delta(cur: float | None, key: str) -> tuple[str, str]:
        if cur is None or len(history) < 2 or history[-2].get(key) is None:
            return ("без изменений", "")
        diff = cur - history[-2][key]
        if abs(diff) < 0.001:
            return ("без изменений", "")
        return (f"{diff:+.3f} за 24ч", "up" if diff > 0 else "down")

    p50_d = sim_delta(sim_p50, "sim_p50")
    p75_d = sim_delta(sim_p75, "sim_p75")
    p95_d = sim_delta(sim_p95, "sim_p95")

    most_connected = metrics.get("most_connected")
    isolated = metrics.get("isolated")
    closest = metrics.get("closest_pair")

    return {
        "pages":     {"value": pages,                "delta": pages_delta[0],   "delta_class": pages_delta[1]},
        "wikilinks": {"value": metrics["wikilinks"], "delta": links_delta[0],   "delta_class": links_delta[1]},
        "domains":   {"value": len(metrics["domains_dist"]), "delta": "без изменений", "delta_class": ""},
        "orphans":   {"value": metrics["orphans"],   "delta": orphans_delta[0], "delta_class": orphans_delta[1]},

        "most_connected": {
            "value": most_connected["name"] if most_connected else "—",
            "delta": f"{most_connected['inbound']} входящих" if most_connected else "—",
        },
        "top_bridge": {
            "value": top_bridge["name"] if top_bridge else "—",
            "delta": f"P={top_bridge['participation']:.2f} · {top_bridge['spans']} сообщ." if top_bridge else "—",
        } if top_bridge else {"value": "—", "delta": "—"},
        "density":      {"value": f"{density:.2f}", "delta": density_delta, "delta_class": density_delta_cls},
        "Q":            {"value": f"{Q:.3f}" if Q is not None else "—", "delta": Q_delta, "delta_class": Q_delta_cls},
        "communities":  {"value": topology.get("communities", 0), "delta": "без изменений"},
        "bridges":      {"value": topology.get("bridges", 0),     "delta": "без изменений"},

        "sim_p50": {"value": f"{sim_p50:.3f}" if sim_p50 is not None else "—", "delta": p50_d[0], "delta_class": p50_d[1]},
        "sim_p75": {"value": f"{sim_p75:.3f}" if sim_p75 is not None else "—", "delta": p75_d[0], "delta_class": p75_d[1]},
        "sim_p95": {"value": f"{sim_p95:.3f}" if sim_p95 is not None else "—", "delta": p95_d[0], "delta_class": p95_d[1]},
        "closest_pair": {
            "value": f"{closest[0]} ↔ {closest[1]}" if closest else "—",
            "delta": f"{closest[2]:.3f}" if closest else "—",
        },
        "isolated": {
            "value": isolated[0] if isolated else "—",
            "delta": f"max {isolated[1]:.3f}" if isolated else "—",
        },
    }


def merge_heavy_data(prev_html: str | None) -> dict[str, Any]:
    """Extract sankey/treemap/insights from previous vault-explorer.html
    so light updates don't wipe them out.
    """
    out = {"sankey": [], "treemap": [], "insights": {}}
    if not prev_html:
        return out
    m = re.search(
        r'<script id="dashboard-data" type="application/json">(.+?)</script>',
        prev_html, re.DOTALL,
    )
    if not m:
        return out
    try:
        prev = json.loads(m.group(1))
    except json.JSONDecodeError:
        return out
    for k in ("sankey", "treemap", "insights"):
        if k in prev and prev[k]:
            out[k] = prev[k]
    return out


# ────────────────────────────────────────────────────────────────────────
# Main pipeline
# ────────────────────────────────────────────────────────────────────────


def compute_all_metrics(pages: list[Page]) -> dict[str, Any]:
    outgoing, incoming = build_wikilink_graph(pages)
    total_wikilinks = sum(len(targets) for targets in outgoing.values())
    page_names = [p.name for p in pages if p.fm and p.fm.fields.get("type") in CONTENT_TYPES]

    orphans = sum(1 for p in pages if p.fm and p.fm.fields.get("type") in CONTENT_TYPES and not incoming.get(p.name))

    # Most-connected (by inbound count)
    in_counts = [(name, len(incoming.get(name, ()))) for name in page_names]
    in_counts.sort(key=lambda x: -x[1])
    most_connected = None
    if in_counts and in_counts[0][1] > 0:
        most_connected = {"name": in_counts[0][0], "inbound": in_counts[0][1]}

    distributions = compute_distributions(pages)
    embeddings = load_embeddings()

    sims, tightest = pairwise_similarity(embeddings) if embeddings else ([], None)
    sim_p50 = percentile(sims, 50) if sims else None
    sim_p75 = percentile(sims, 75) if sims else None
    sim_p95 = percentile(sims, 95) if sims else None
    isolated = most_isolated_page(embeddings) if embeddings else None
    cohesion = domain_cohesion(pages, embeddings) if embeddings else {}

    topology = compute_topology(outgoing, incoming)

    return {
        "pages_total": len(page_names),
        "pages_list": page_names,
        "wikilinks": total_wikilinks,
        "orphans": orphans,
        "most_connected": most_connected,
        "types_dist": distributions["types"],
        "domains_dist": distributions["domains"],
        "provenance_dist": distributions["provenance"],
        "statuses_dist": distributions["statuses"],
        "unassigned": distributions["unassigned"],
        "Q": topology.get("Q"),
        "communities": topology.get("communities", 0),
        "bridges": topology.get("bridges", 0),
        "topology": topology,
        "sim_p50": sim_p50,
        "sim_p75": sim_p75,
        "sim_p95": sim_p95,
        "closest_pair": tightest,
        "isolated": isolated,
        "cohesion": cohesion,
    }


def history_entry_to_jsdata(entry: dict[str, Any]) -> dict[str, Any]:
    """Strip heavy field `pages_list` from history entries before
    embedding in the HTML (frontend doesn't need page-name lists)."""
    out = {k: v for k, v in entry.items() if k != "pages_list"}
    return out


def load_vendor_js() -> str:
    """Concatenate vendored Chart.js + plugins for inlining into the
    HTML. Resolves the perennial 'relative paths don't work inside
    Obsidian HTML-viewer plugins' problem — at the cost of ~230 KB
    per rendered file, the page loads in any sandbox."""
    chunks: list[str] = []
    for name in VENDOR_JS_FILES:
        src = BIN_VENDOR_DIR / name
        if not src.exists():
            continue
        chunks.append(f"/* === {name} === */\n" + src.read_text(encoding="utf-8"))
    return "\n".join(chunks)


def render_dashboard(data: dict[str, Any]) -> None:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    # Escape closing script tag — JSON could contain "</script>" inside strings.
    payload = payload.replace("</", "<\\/")
    result = template.replace("{{DATA_JSON}}", payload)
    result = result.replace("{{VENDOR_JS}}", load_vendor_js())
    OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(result, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trigger", default="auto", help="trigger label for the history entry")
    args = ap.parse_args()

    pages = discover_pages()
    if not pages:
        print("no wiki pages found", file=sys.stderr)
        return 1

    metrics = compute_all_metrics(pages)
    wordcloud = compute_wordcloud(pages)

    history = load_history()
    prev = history[-1] if history else None
    entry = build_history_entry(metrics, args.trigger, prev)
    history = update_history(history, entry)
    save_history(history)

    snap = find_latest_snapshot()
    cards = build_cards(metrics, history)
    health = compute_health(metrics)

    prev_html = OUTPUT_HTML.read_text(encoding="utf-8") if OUTPUT_HTML.exists() else None
    heavy = merge_heavy_data(prev_html)

    data = {
        "now": dt.datetime.now().isoformat(timespec="minutes"),
        "vault_name": VAULT_NAME,
        "history": [history_entry_to_jsdata(h) for h in history],
        "provenance": metrics["provenance_dist"],
        "statuses": metrics["statuses_dist"],
        "wordcloud": wordcloud,
        "sankey": heavy["sankey"],
        "treemap": heavy["treemap"],
        "insights": heavy["insights"],
        "latest_snapshot_date": snap["date"],
        "umap_iframe": snap["umap_iframe"],
        "graph_iframe": snap["graph_iframe"],
        "cards": cards,
        "health": health,
    }
    render_dashboard(data)
    print(f"wrote {OUTPUT_HTML} (health: {health['total']}, {metrics['pages_total']} pages, {metrics['wikilinks']} wikilinks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
