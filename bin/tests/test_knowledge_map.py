"""Unit tests for bin/knowledge_map.py.

Covers data preparation, color blending, edge construction, statistics,
and artifact-page rendering. The viz parts (UMAP projection, Cytoscape
HTML rendering) are not unit-tested — they depend on optional packages
and randomness; smoke-test via live run instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knowledge_map import (
    PageInfo,
    _is_content_page,
    assign_domain_colors,
    blend_domain_colors,
    build_dataset,
    build_edges,
    collect_domains,
    compute_statistics,
    generate_distinct_palette,
    hex_to_rgb,
    rgb_to_hex,
)
from embed import EmbedIndex
from static_lint import parse_frontmatter, Page


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────


def make_page(
    name: str = "Test",
    folder: str = "ideas",
    fm_yaml: str = "",
    body: str = "",
) -> Page:
    text = f"---\n{fm_yaml}\n---\n{body}" if fm_yaml else body
    fm, parsed_body = parse_frontmatter(text)
    path = (
        Path(f"wiki/{folder}/{name}.md") if folder else Path(f"wiki/{name}.md")
    )
    return Page(path=path, folder=folder, name=name, text=text, fm=fm, body=parsed_body)


def make_idx(pairs: list[tuple[str, list[float]]]) -> EmbedIndex:
    idx = EmbedIndex(Path("/dev/null/missing"))
    for name, vec in pairs:
        idx.upsert(name, f"content for {name}", vec)
    return idx


# ────────────────────────────────────────────────────────────────────────
# _is_content_page
# ────────────────────────────────────────────────────────────────────────


class TestIsContentPage:
    def test_idea_is_content(self):
        p = make_page(folder="ideas", fm_yaml="type: idea")
        assert _is_content_page(p) is True

    def test_meta_excluded_by_type(self):
        p = make_page(folder="meta", fm_yaml="type: meta")
        assert _is_content_page(p) is False

    def test_root_file_excluded(self):
        p = make_page(folder="", name="cache", fm_yaml="type: meta")
        assert _is_content_page(p) is False

    def test_meta_folder_excluded_even_without_type(self):
        p = make_page(folder="meta", fm_yaml="")
        assert _is_content_page(p) is False


# ────────────────────────────────────────────────────────────────────────
# Color helpers
# ────────────────────────────────────────────────────────────────────────


class TestColorHelpers:
    def test_hex_to_rgb_basic(self):
        assert hex_to_rgb("#ff0000") == (255, 0, 0)
        assert hex_to_rgb("00ff00") == (0, 255, 0)
        assert hex_to_rgb("#0000ff") == (0, 0, 255)

    def test_hex_to_rgb_uppercase(self):
        assert hex_to_rgb("#FFFFFF") == (255, 255, 255)

    def test_hex_to_rgb_handles_rgb_function(self):
        # Some palettes return CSS-style "rgb(127, 60, 141)" instead of hex.
        assert hex_to_rgb("rgb(127, 60, 141)") == (127, 60, 141)
        assert hex_to_rgb("rgb(0,0,0)") == (0, 0, 0)
        assert hex_to_rgb("rgb(255, 255, 255)") == (255, 255, 255)

    def test_rgb_to_hex_roundtrip(self):
        for h in ["#000000", "#ff8800", "#abcdef"]:
            assert rgb_to_hex(hex_to_rgb(h)) == h

    def test_assign_domain_colors_stable(self):
        # Same palette + same domains → same assignment, regardless of input order
        a = assign_domain_colors(["RL", "ML"], ["#aaa", "#bbb"])
        b = assign_domain_colors(["ML", "RL"], ["#aaa", "#bbb"])
        assert a == b

    def test_assign_domain_colors_cycles_palette(self):
        out = assign_domain_colors(
            ["A", "B", "C", "D"], ["#aaa", "#bbb"],
        )
        # 4 domains with 2-color palette → wrap around
        assert len(set(out.values())) == 2

    def test_blend_single_domain(self):
        out = blend_domain_colors(["X"], {"X": "#ff0000"})
        assert out == "#ff0000"

    def test_blend_two_complementary_stays_saturated(self):
        # HSL blending: yellow + blue lie on opposite sides of the hue
        # wheel. RGB-averaging would yield desaturated gray. HSL hue
        # averaging yields a between-hue (green) at the same saturation.
        # Verify property: the blend is NOT gray (saturation > 0.3).
        out = blend_domain_colors(
            ["A", "B"], {"A": "#e4bb67", "B": "#6791e4"},  # ML + IR from our palette
        )
        import colorsys
        r, g, b = (int(out[i:i+2], 16) / 255 for i in (1, 3, 5))
        _, _, sat = colorsys.rgb_to_hls(r, g, b)
        assert sat > 0.3, f"complementary blend faded to gray: {out} (S={sat:.2f})"

    def test_blend_two_adjacent_hues_averages_between(self):
        # Adjacent hues on color wheel (yellow + green) blend to yellow-green.
        # Verify property: result hue is between the two input hues.
        import colorsys
        a_hex = "#e4bb67"  # 40° (yellow)
        b_hex = "#67e47c"  # 130° (green)
        out = blend_domain_colors(["A", "B"], {"A": a_hex, "B": b_hex})
        r, g, b = (int(out[i:i+2], 16) / 255 for i in (1, 3, 5))
        h_out, _, _ = colorsys.rgb_to_hls(r, g, b)
        # Should be roughly 85° (between 40° and 130°)
        assert 0.10 < h_out < 0.30, f"blend hue {h_out*360:.1f}° not between 40° and 130°"

    def test_blend_no_domains_returns_default(self):
        assert blend_domain_colors([], {}) == "#cccccc"

    def test_blend_unknown_domain_returns_default(self):
        # Domain string not in color map
        assert blend_domain_colors(["Unknown"], {"Other": "#fff"}) == "#cccccc"

    def test_blend_custom_default(self):
        assert blend_domain_colors([], {}, default="#000") == "#000"

    def test_blend_preserves_saturation_for_real_palette(self):
        # End-to-end integration: with the actual generated palette,
        # blending any pair of complementary domains must NOT produce gray.
        from knowledge_map import generate_distinct_palette, assign_domain_colors
        palette = generate_distinct_palette(4)
        d2c = assign_domain_colors(["A", "B", "C", "D"], palette)
        domains = list(d2c.keys())
        import colorsys
        for i in range(len(domains)):
            for j in range(i + 1, len(domains)):
                blend = blend_domain_colors([domains[i], domains[j]], d2c)
                r, g, b = (int(blend[k:k+2], 16) / 255 for k in (1, 3, 5))
                _, _, sat = colorsys.rgb_to_hls(r, g, b)
                assert sat > 0.25, (
                    f"blend of {domains[i]}+{domains[j]} = {blend} "
                    f"too desaturated (S={sat:.2f})"
                )

    def test_blend_distinct_from_any_pure_domain_color(self):
        # For evenly-spaced palettes, midpoint hues can coincide with
        # other domains. We compensate by darkening the blend so it
        # doesn't visually merge with a pure-color cluster.
        # Property: distance from blend to any pure domain color is
        # noticeable (Euclidean RGB distance > a threshold).
        from knowledge_map import generate_distinct_palette, assign_domain_colors
        palette = generate_distinct_palette(4)
        d2c = assign_domain_colors(["A", "B", "C", "D"], palette)
        domains = list(d2c.keys())

        def rgb(hex_): return tuple(int(hex_[k:k+2], 16) for k in (1, 3, 5))
        def dist(c1, c2):
            return sum((a - b) ** 2 for a, b in zip(rgb(c1), rgb(c2))) ** 0.5

        for i in range(len(domains)):
            for j in range(i + 1, len(domains)):
                blend = blend_domain_colors([domains[i], domains[j]], d2c)
                for d in domains:
                    pure = d2c[d]
                    if pure == blend:
                        # Byte-equal — bad
                        assert False, f"blend {blend} equals pure {d}={pure}"
                    # Require ≥30 RGB-distance: not just visually different
                    # in one channel, but globally distinct
                    assert dist(blend, pure) > 30, (
                        f"blend of {domains[i]}+{domains[j]} = {blend} "
                        f"too close to pure {d} = {pure} (dist={dist(blend, pure):.1f})"
                    )


class TestGenerateDistinctPalette:
    """Hue-spacing generator. Verify mathematical properties of the output,
    not specific color values (those depend on HSL conversion roundoff)."""

    def test_returns_n_colors(self):
        for n in (1, 2, 3, 4, 8, 12):
            assert len(generate_distinct_palette(n)) == n

    def test_zero_or_negative_returns_empty(self):
        assert generate_distinct_palette(0) == []
        assert generate_distinct_palette(-3) == []

    def test_all_outputs_are_valid_hex(self):
        for color in generate_distinct_palette(6):
            assert color.startswith("#") and len(color) == 7
            int(color[1:], 16)  # raises if not hex

    def test_colors_are_distinct(self):
        # 8 evenly-spaced hues must all differ from each other
        colors = generate_distinct_palette(8)
        assert len(set(colors)) == 8

    def test_hues_evenly_spaced(self):
        # Convert each color back to HSL hue, verify spacing ≈ 360/n.
        import colorsys
        n = 6
        colors = generate_distinct_palette(n)
        hues = []
        for c in colors:
            r, g, b = (int(c[1:3], 16) / 255, int(c[3:5], 16) / 255, int(c[5:7], 16) / 255)
            h, _, _ = colorsys.rgb_to_hls(r, g, b)
            hues.append(h * 360)
        # Sort, compute pairwise differences (cyclic)
        hues.sort()
        gaps = [hues[i + 1] - hues[i] for i in range(len(hues) - 1)]
        gaps.append(360 - hues[-1] + hues[0])
        expected_gap = 360 / n
        for g in gaps:
            assert abs(g - expected_gap) < 2.0, f"gap {g} != {expected_gap}"

    def test_lightness_avoids_dark_bg_invisibility(self):
        # All colors should have lightness >= 0.5 so they're visible on
        # the dark dashboard background.
        import colorsys
        for c in generate_distinct_palette(8):
            r, g, b = (int(c[1:3], 16) / 255, int(c[3:5], 16) / 255, int(c[5:7], 16) / 255)
            _, lightness, _ = colorsys.rgb_to_hls(r, g, b)
            assert lightness >= 0.5, f"{c} too dark for dark bg (L={lightness:.2f})"


# ────────────────────────────────────────────────────────────────────────
# build_dataset
# ────────────────────────────────────────────────────────────────────────


class TestBuildDataset:
    def test_excludes_meta_pages(self):
        meta = make_page(folder="meta", name="cache", fm_yaml="type: meta")
        idea = make_page(name="A", fm_yaml="type: idea")
        infos = build_dataset([meta, idea], make_idx([]))
        names = [info.name for info in infos]
        assert "cache" not in names
        assert "A" in names

    def test_extracts_domains_from_frontmatter(self):
        p = make_page(
            name="A",
            fm_yaml='type: idea\ndomain:\n  - "[[ML]]"\n  - "[[RL]]"',
        )
        infos = build_dataset([p], make_idx([]))
        assert sorted(infos[0].domains) == ["ML", "RL"]

    def test_normalizes_path_prefixed_domain(self):
        # [[wiki/domains/ML]] should normalize to "ML"
        p = make_page(
            name="A",
            fm_yaml='type: idea\ndomain:\n  - "[[wiki/domains/ML]]"',
        )
        infos = build_dataset([p], make_idx([]))
        assert infos[0].domains == ["ML"]

    def test_attaches_vec_when_present(self):
        p = make_page(name="A", fm_yaml="type: idea")
        idx = make_idx([("A", [0.1, 0.2])])
        infos = build_dataset([p], idx)
        assert infos[0].vec == [0.1, 0.2]

    def test_vec_none_when_absent(self):
        p = make_page(name="A", fm_yaml="type: idea")
        infos = build_dataset([p], make_idx([]))
        assert infos[0].vec is None

    def test_extracts_body_links(self):
        p = make_page(name="A", fm_yaml="type: idea", body="See [[B]] and [[C]].\n")
        infos = build_dataset([p], make_idx([]))
        assert infos[0].body_links == {"B", "C"}

    def test_excludes_raw_links_from_body(self):
        p = make_page(
            name="A", fm_yaml="type: idea",
            body="See [[B]] and [[raw/X]].\n",
        )
        infos = build_dataset([p], make_idx([]))
        assert infos[0].body_links == {"B"}

    def test_extracts_fm_related(self):
        p = make_page(
            name="A",
            fm_yaml='type: idea\nrelated:\n  - "[[B]]"\n  - "[[C]]"',
        )
        infos = build_dataset([p], make_idx([]))
        assert infos[0].fm_links == {"B", "C"}

    def test_inbound_computed_from_outbound(self):
        a = make_page(name="A", fm_yaml="type: idea", body="See [[B]].\n")
        b = make_page(name="B", fm_yaml="type: idea")
        infos = build_dataset([a, b], make_idx([]))
        b_info = next(i for i in infos if i.name == "B")
        assert b_info.inbound == {"A"}

    def test_inbound_counts_fm_related_too(self):
        a = make_page(name="A", fm_yaml='type: idea\nrelated:\n  - "[[B]]"')
        b = make_page(name="B", fm_yaml="type: idea")
        infos = build_dataset([a, b], make_idx([]))
        b_info = next(i for i in infos if i.name == "B")
        assert "A" in b_info.inbound


# ────────────────────────────────────────────────────────────────────────
# collect_domains / build_edges
# ────────────────────────────────────────────────────────────────────────


class TestCollectDomains:
    def test_counts_pages_per_domain(self):
        infos = [
            PageInfo("A", "wiki/ideas/A.md", "idea", "ideas", ["ML"], None),
            PageInfo("B", "wiki/ideas/B.md", "idea", "ideas", ["ML", "RL"], None),
            PageInfo("C", "wiki/ideas/C.md", "idea", "ideas", [], None),
        ]
        counts = collect_domains(infos)
        assert counts == {"ML": 2, "RL": 1}


class TestBuildEdges:
    def test_dedup_undirected(self):
        infos = [
            PageInfo("A", "wiki/ideas/A.md", "idea", "ideas", [], None,
                     body_links={"B"}, fm_links=set()),
            PageInfo("B", "wiki/ideas/B.md", "idea", "ideas", [], None,
                     body_links={"A"}, fm_links=set()),
        ]
        edges = build_edges(infos)
        assert edges == [(0, 1)]

    def test_one_directional_link(self):
        infos = [
            PageInfo("A", "wiki/ideas/A.md", "idea", "ideas", [], None,
                     body_links={"B"}, fm_links=set()),
            PageInfo("B", "wiki/ideas/B.md", "idea", "ideas", [], None,
                     body_links=set(), fm_links=set()),
        ]
        edges = build_edges(infos)
        assert edges == [(0, 1)]

    def test_self_loops_dropped(self):
        infos = [
            PageInfo("A", "wiki/ideas/A.md", "idea", "ideas", [], None,
                     body_links={"A"}, fm_links=set()),
        ]
        assert build_edges(infos) == []

    def test_links_to_non_content_dropped(self):
        # Link to "Ghost" which isn't in the dataset → no edge
        infos = [
            PageInfo("A", "wiki/ideas/A.md", "idea", "ideas", [], None,
                     body_links={"Ghost"}, fm_links=set()),
        ]
        assert build_edges(infos) == []

    def test_body_and_fm_links_combined(self):
        infos = [
            PageInfo("A", "wiki/ideas/A.md", "idea", "ideas", [], None,
                     body_links={"B"}, fm_links={"C"}),
            PageInfo("B", "wiki/ideas/B.md", "idea", "ideas", [], None,
                     body_links=set(), fm_links=set()),
            PageInfo("C", "wiki/ideas/C.md", "idea", "ideas", [], None,
                     body_links=set(), fm_links=set()),
        ]
        assert sorted(build_edges(infos)) == [(0, 1), (0, 2)]


# ────────────────────────────────────────────────────────────────────────
# compute_statistics
# ────────────────────────────────────────────────────────────────────────


class TestComputeStatistics:
    def test_type_counts(self):
        infos = [
            PageInfo("A", "wiki/ideas/A.md", "idea", "ideas", [], None),
            PageInfo("B", "wiki/ideas/B.md", "idea", "ideas", [], None),
            PageInfo("E", "wiki/entities/E.md", "entity", "entities", [], None),
        ]
        stats = compute_statistics(infos)
        assert stats["type_counts"] == {"idea": 2, "entity": 1}

    def test_unassigned_counted(self):
        infos = [
            PageInfo("A", "wiki/ideas/A.md", "idea", "ideas", ["ML"], None),
            PageInfo("B", "wiki/ideas/B.md", "idea", "ideas", [], None),
        ]
        stats = compute_statistics(infos)
        assert stats["unassigned"] == 1

    def test_orphans_listed(self):
        a = PageInfo("A", "wiki/ideas/A.md", "idea", "ideas", [], None)
        b = PageInfo("B", "wiki/ideas/B.md", "idea", "ideas", [], None)
        c = PageInfo("C", "wiki/ideas/C.md", "idea", "ideas", [], None,
                     body_links={"A"})
        # After build_dataset would set inbound, but we're testing
        # compute_statistics in isolation. Manually set:
        a.inbound = {"C"}
        # B and C have no inbound
        stats = compute_statistics([a, b, c])
        assert "B" in stats["orphans"]
        assert "C" in stats["orphans"]
        assert "A" not in stats["orphans"]

    def test_most_connected_finds_max_inbound(self):
        a = PageInfo("A", "wiki/ideas/A.md", "idea", "ideas", [], None)
        a.inbound = {"X", "Y", "Z"}
        b = PageInfo("B", "wiki/ideas/B.md", "idea", "ideas", [], None)
        b.inbound = {"X"}
        stats = compute_statistics([a, b])
        names = [p["name"] for p in stats["most_connected"]]
        assert "A" in names
        assert "B" not in names

    def test_no_inbound_no_most_connected(self):
        a = PageInfo("A", "wiki/ideas/A.md", "idea", "ideas", [], None)
        b = PageInfo("B", "wiki/ideas/B.md", "idea", "ideas", [], None)
        stats = compute_statistics([a, b])
        assert stats["most_connected"] == []

    def test_tightest_pair_identified(self):
        a = PageInfo("A", "wiki/ideas/A.md", "idea", "ideas", [], [1.0, 0.0])
        b = PageInfo("B", "wiki/ideas/B.md", "idea", "ideas", [], [0.99, 0.01])
        c = PageInfo("C", "wiki/ideas/C.md", "idea", "ideas", [], [0.0, 1.0])
        stats = compute_statistics([a, b, c])
        assert stats["tightest_pair"] is not None
        a_, b_, _ = stats["tightest_pair"]
        assert {a_, b_} == {"A", "B"}

    def test_most_isolated_minimizes_max_sim(self):
        a = PageInfo("A", "wiki/ideas/A.md", "idea", "ideas", [], [1.0, 0.0])
        b = PageInfo("B", "wiki/ideas/B.md", "idea", "ideas", [], [0.99, 0.01])
        c = PageInfo("C", "wiki/ideas/C.md", "idea", "ideas", [], [0.0, 1.0])
        stats = compute_statistics([a, b, c])
        # C is orthogonal to both A and B → most isolated
        n, _ = stats["most_isolated"]
        assert n == "C"

    def test_domain_avg_cosine_per_domain(self):
        # ML domain: A and B are very similar
        # RL domain: C alone (skipped — need ≥2 members)
        a = PageInfo("A", "wiki/ideas/A.md", "idea", "ideas", ["ML"], [1.0, 0.0])
        b = PageInfo("B", "wiki/ideas/B.md", "idea", "ideas", ["ML"], [0.99, 0.01])
        c = PageInfo("C", "wiki/ideas/C.md", "idea", "ideas", ["RL"], [0.0, 1.0])
        stats = compute_statistics([a, b, c])
        assert "ML" in stats["domain_avg_cosine"]
        assert stats["domain_avg_cosine"]["ML"] > 0.9
        assert "RL" not in stats["domain_avg_cosine"]  # only 1 member

    def test_no_vecs_no_semantic_stats(self):
        a = PageInfo("A", "wiki/ideas/A.md", "idea", "ideas", [], None)
        stats = compute_statistics([a])
        assert stats["sim_count"] == 0
        assert stats["tightest_pair"] is None


