"""Unit tests for bin/embed.py — embedding service.

Coverage:
- Vector math: cosine, vec_mean, percentile
- Frontmatter stripping & content_hash stability
- EmbedIndex: load/save roundtrip, hash-based invalidation, prune, top_k
- update_index: re-embeds only changed, model-change invalidation
- StubEmbedder for deterministic tests (no Ollama dependency)
- OllamaEmbedder: HTTP mocking for new + legacy endpoints
- Graceful degradation: EmbedderUnavailable on connection failure
- Page discovery: respects raw/formats/, raw/meta/, lint-report files
"""

from __future__ import annotations

import json
import math
import sys
import urllib.error
from pathlib import Path
from typing import Sequence
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import embed as E
from embed import (
    Embedder,
    EmbedderError,
    EmbedderUnavailable,
    EmbedIndex,
    EmbedRecord,
    OllamaEmbedder,
    OpenAIEmbedder,
    _filter_and_format_results,
    _format_result_line,
    content_hash,
    cosine,
    discover_raw_pages,
    discover_wiki_pages,
    parse_index_summaries,
    percentile,
    strip_frontmatter,
    update_index,
    vec_mean,
    wiki_page_paths,
)


# ────────────────────────────────────────────────────────────────────────
# Test stub
# ────────────────────────────────────────────────────────────────────────


class StubEmbedder(Embedder):
    """Deterministic embedder for tests: hashes text → pseudo-vector.

    Identical text → identical vector. No network. Counts calls so tests
    can assert how many embeddings were actually computed.
    """

    def __init__(self, dim: int = 8):
        self.dim = dim
        self.calls = 0
        self.last_text: str | None = None

    def embed(self, text: str) -> list[float]:
        self.calls += 1
        self.last_text = text
        h = content_hash(text)
        # Take first dim*2 hex chars → dim bytes → floats in [0, 1)
        ints = [int(h[i : i + 2], 16) for i in range(0, self.dim * 2, 2)]
        return [x / 255.0 for x in ints]


class FailingEmbedder(Embedder):
    """Always raises EmbedderUnavailable. For graceful-degradation tests."""

    def embed(self, text: str) -> list[float]:
        raise EmbedderUnavailable("simulated outage")


# ────────────────────────────────────────────────────────────────────────
# Vector math
# ────────────────────────────────────────────────────────────────────────


class TestCosine:
    def test_identical(self):
        v = [1.0, 2.0, 3.0]
        assert cosine(v, v) == pytest.approx(1.0)

    def test_orthogonal(self):
        assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite(self):
        assert cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_zero_vector_returns_zero(self):
        assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0

    def test_dim_mismatch_raises(self):
        with pytest.raises(ValueError):
            cosine([1.0, 2.0], [1.0, 2.0, 3.0])


class TestVecMean:
    def test_basic(self):
        result = vec_mean([[1.0, 2.0], [3.0, 4.0]])
        assert result == [2.0, 3.0]

    def test_single(self):
        assert vec_mean([[1.0, 2.0, 3.0]]) == [1.0, 2.0, 3.0]

    def test_empty(self):
        assert vec_mean([]) == []

    def test_inconsistent_dims_raises(self):
        with pytest.raises(ValueError):
            vec_mean([[1.0], [1.0, 2.0]])


class TestPercentile:
    def test_median(self):
        assert percentile([1, 2, 3, 4, 5], 50) == 3.0

    def test_min(self):
        assert percentile([10, 20, 30], 0) == 10.0

    def test_max(self):
        assert percentile([10, 20, 30], 100) == 30.0

    def test_interpolation(self):
        # 50th of [1, 2] → midpoint
        assert percentile([1.0, 2.0], 50) == pytest.approx(1.5)

    def test_empty(self):
        assert percentile([], 50) == 0.0

    def test_single(self):
        assert percentile([42.0], 75) == 42.0


# ────────────────────────────────────────────────────────────────────────
# Frontmatter stripping & hashing
# ────────────────────────────────────────────────────────────────────────


class TestStripFrontmatter:
    def test_removes_frontmatter(self):
        text = "---\ntype: idea\n---\nbody text"
        assert strip_frontmatter(text) == "body text"

    def test_no_frontmatter_unchanged(self):
        text = "# Heading\n\ncontent"
        assert strip_frontmatter(text) == text

    def test_only_first_block_stripped(self):
        # Body may legitimately contain --- as horizontal rule
        text = "---\ntype: idea\n---\nbefore\n\n---\n\nafter"
        result = strip_frontmatter(text)
        assert "type: idea" not in result
        assert "before" in result
        assert "after" in result


class TestContentHash:
    def test_deterministic(self):
        assert content_hash("hello") == content_hash("hello")

    def test_different_content_different_hash(self):
        assert content_hash("a") != content_hash("b")

    def test_frontmatter_changes_ignored(self):
        # Same body, different frontmatter → same hash (frontmatter is stripped)
        a = "---\nstatus: ready\n---\nbody"
        b = "---\nstatus: in-progress\ntags: [ML]\n---\nbody"
        assert content_hash(a) == content_hash(b)

    def test_body_changes_detected(self):
        a = "---\ntype: idea\n---\nbody one"
        b = "---\ntype: idea\n---\nbody two"
        assert content_hash(a) != content_hash(b)

    def test_returns_64_char_hex(self):
        h = content_hash("anything")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


# ────────────────────────────────────────────────────────────────────────
# EmbedIndex
# ────────────────────────────────────────────────────────────────────────


class TestEmbedIndex:
    def test_empty_load(self, tmp_path):
        idx = EmbedIndex(tmp_path / "missing.json")
        idx.load()
        assert idx.items == {}
        assert idx.model is None

    def test_save_and_reload_roundtrip(self, tmp_path):
        path = tmp_path / "embeddings.json"
        idx = EmbedIndex(path)
        idx.model = "frida"
        idx.upsert("Page-A", "content A", [0.1, 0.2, 0.3])
        idx.upsert("Page-B", "content B", [0.4, 0.5, 0.6])
        idx.save()

        loaded = EmbedIndex(path)
        loaded.load()
        assert loaded.model == "frida"
        assert "Page-A" in loaded.items
        assert loaded.items["Page-A"].vec == [0.1, 0.2, 0.3]

    def test_save_pretty_json(self, tmp_path):
        path = tmp_path / "out.json"
        idx = EmbedIndex(path)
        idx.upsert("X", "data", [0.0])
        idx.save()
        text = path.read_text()
        # Indented (pretty-printed)
        assert "\n  " in text

    def test_save_sorts_items(self, tmp_path):
        path = tmp_path / "out.json"
        idx = EmbedIndex(path)
        idx.upsert("Zebra", "z", [0.0])
        idx.upsert("Apple", "a", [0.0])
        idx.save()
        text = path.read_text()
        # Apple appears before Zebra
        assert text.index("Apple") < text.index("Zebra")

    def test_corrupted_json_load_silent(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not valid json")
        idx = EmbedIndex(path)
        idx.load()
        assert idx.items == {}

    def test_needs_update_new_entry(self, tmp_path):
        idx = EmbedIndex(tmp_path / "x.json")
        assert idx.needs_update("Page", "any content") is True

    def test_needs_update_unchanged(self, tmp_path):
        idx = EmbedIndex(tmp_path / "x.json")
        content = "the body"
        idx.upsert("Page", content, [0.1])
        assert idx.needs_update("Page", content) is False

    def test_needs_update_changed(self, tmp_path):
        idx = EmbedIndex(tmp_path / "x.json")
        idx.upsert("Page", "old content", [0.1])
        assert idx.needs_update("Page", "new content") is True

    def test_remove_stale(self, tmp_path):
        idx = EmbedIndex(tmp_path / "x.json")
        idx.upsert("Keep", "k", [0.1])
        idx.upsert("Drop1", "d", [0.1])
        idx.upsert("Drop2", "d", [0.1])
        removed = idx.remove_stale({"Keep"})
        assert removed == 2
        assert set(idx.items) == {"Keep"}

    def test_get_existing(self, tmp_path):
        idx = EmbedIndex(tmp_path / "x.json")
        idx.upsert("X", "data", [1.0, 2.0])
        assert idx.get("X") == [1.0, 2.0]

    def test_get_missing_returns_none(self, tmp_path):
        idx = EmbedIndex(tmp_path / "x.json")
        assert idx.get("Nope") is None

    def test_top_k_returns_closest(self, tmp_path):
        idx = EmbedIndex(tmp_path / "x.json")
        idx.upsert("Same", "s", [1.0, 0.0])
        idx.upsert("Orth", "o", [0.0, 1.0])
        idx.upsert("Opp", "p", [-1.0, 0.0])
        results = idx.top_k([1.0, 0.0], k=10)
        names = [n for n, _ in results]
        assert names[0] == "Same"
        assert names[-1] == "Opp"

    def test_top_k_exclude(self, tmp_path):
        idx = EmbedIndex(tmp_path / "x.json")
        idx.upsert("A", "a", [1.0, 0.0])
        idx.upsert("B", "b", [0.9, 0.1])
        results = idx.top_k([1.0, 0.0], k=10, exclude={"A"})
        names = [n for n, _ in results]
        assert "A" not in names
        assert "B" in names

    def test_top_k_limit(self, tmp_path):
        idx = EmbedIndex(tmp_path / "x.json")
        for i in range(5):
            idx.upsert(f"P{i}", f"c{i}", [float(i), 0.0])
        results = idx.top_k([1.0, 0.0], k=2)
        assert len(results) == 2

    def test_pairwise_similarities_count(self, tmp_path):
        idx = EmbedIndex(tmp_path / "x.json")
        for i in range(4):
            idx.upsert(f"P{i}", f"c{i}", [float(i), 1.0])
        sims = idx.all_pairwise_similarities()
        # C(4, 2) = 6 pairs
        assert len(sims) == 6

    def test_pairwise_similarities_empty(self, tmp_path):
        idx = EmbedIndex(tmp_path / "x.json")
        assert idx.all_pairwise_similarities() == []


# ────────────────────────────────────────────────────────────────────────
# update_index
# ────────────────────────────────────────────────────────────────────────


class TestUpdateIndex:
    def test_first_run_embeds_all(self, tmp_path):
        idx = EmbedIndex(tmp_path / "x.json")
        emb = StubEmbedder()
        pages = [("A", "content A"), ("B", "content B")]
        u, p = update_index(idx, pages, emb)
        assert u == 2
        assert p == 0
        assert emb.calls == 2

    def test_unchanged_skipped(self, tmp_path):
        idx = EmbedIndex(tmp_path / "x.json")
        emb = StubEmbedder()
        pages = [("A", "content A")]
        update_index(idx, pages, emb)
        emb.calls = 0
        update_index(idx, pages, emb)
        assert emb.calls == 0  # nothing changed

    def test_changed_re_embedded(self, tmp_path):
        idx = EmbedIndex(tmp_path / "x.json")
        emb = StubEmbedder()
        update_index(idx, [("A", "old")], emb)
        emb.calls = 0
        update_index(idx, [("A", "new")], emb)
        assert emb.calls == 1

    def test_stale_pruned(self, tmp_path):
        idx = EmbedIndex(tmp_path / "x.json")
        emb = StubEmbedder()
        update_index(idx, [("A", "a"), ("B", "b")], emb)
        # B no longer in pages
        u, p = update_index(idx, [("A", "a")], emb)
        assert p == 1
        assert "B" not in idx.items

    def test_model_change_invalidates_all(self, tmp_path):
        idx = EmbedIndex(tmp_path / "x.json")
        emb = StubEmbedder()
        update_index(idx, [("A", "a")], emb, model_name="model-1")
        emb.calls = 0
        # Same content, different model → re-embed
        update_index(idx, [("A", "a")], emb, model_name="model-2")
        assert emb.calls == 1
        assert idx.model == "model-2"

    def test_strips_frontmatter_before_embedding(self, tmp_path):
        idx = EmbedIndex(tmp_path / "x.json")
        emb = StubEmbedder()
        text = "---\ntype: idea\n---\nthe body\n"
        update_index(idx, [("A", text)], emb)
        assert emb.last_text is not None
        assert "type: idea" not in emb.last_text
        assert "the body" in emb.last_text

    def test_frontmatter_only_change_skipped(self, tmp_path):
        idx = EmbedIndex(tmp_path / "x.json")
        emb = StubEmbedder()
        a = "---\nstatus: ready\n---\nsame body"
        b = "---\nstatus: in-progress\n---\nsame body"
        update_index(idx, [("A", a)], emb)
        emb.calls = 0
        update_index(idx, [("A", b)], emb)
        assert emb.calls == 0


# ────────────────────────────────────────────────────────────────────────
# OllamaEmbedder (HTTP mocked)
# ────────────────────────────────────────────────────────────────────────


def _mock_response(payload: dict):
    """Build a mock context-manager that mimics urlopen response."""

    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=json.dumps(payload).encode("utf-8"))))
    cm.__exit__ = MagicMock(return_value=False)
    return cm


class TestOllamaEmbedder:
    def test_new_api_embeddings_field(self):
        emb = OllamaEmbedder(host="http://x", model="frida")
        with patch("urllib.request.urlopen", return_value=_mock_response({"embeddings": [[0.1, 0.2, 0.3]]})):
            result = emb.embed("text")
        assert result == [0.1, 0.2, 0.3]

    def test_new_api_embedding_field_fallback(self):
        # Some servers return single "embedding" key on /api/embed
        emb = OllamaEmbedder(host="http://x", model="frida")
        with patch("urllib.request.urlopen", return_value=_mock_response({"embedding": [0.5, 0.6]})):
            result = emb.embed("text")
        assert result == [0.5, 0.6]

    def test_legacy_fallback_on_404(self):
        emb = OllamaEmbedder(host="http://x", model="frida")

        # First call: 404 from /api/embed; second call: legacy success
        responses = [
            urllib.error.HTTPError("http://x/api/embed", 404, "Not Found", {}, None),
            _mock_response({"embedding": [0.7, 0.8]}),
        ]
        with patch("urllib.request.urlopen", side_effect=responses):
            result = emb.embed("text")
        assert result == [0.7, 0.8]
        assert emb._use_legacy is True

    def test_legacy_sticky_after_first_404(self):
        emb = OllamaEmbedder(host="http://x", model="frida")
        responses = [
            urllib.error.HTTPError("http://x/api/embed", 404, "Not Found", {}, None),
            _mock_response({"embedding": [0.1]}),
            _mock_response({"embedding": [0.2]}),
        ]
        with patch("urllib.request.urlopen", side_effect=responses):
            emb.embed("first")
            emb.embed("second")
        # After detecting legacy, no further /api/embed attempts
        assert emb._use_legacy is True

    def test_url_error_raises_unavailable(self):
        emb = OllamaEmbedder(host="http://nowhere", model="frida")
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
            with pytest.raises(EmbedderUnavailable):
                emb.embed("text")

    def test_connection_error_raises_unavailable(self):
        emb = OllamaEmbedder(host="http://x", model="frida")
        with patch("urllib.request.urlopen", side_effect=ConnectionError("boom")):
            with pytest.raises(EmbedderUnavailable):
                emb.embed("text")

    def test_http_500_raises_error(self):
        emb = OllamaEmbedder(host="http://x", model="frida")
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("http://x", 500, "Internal Error", {}, None),
        ):
            with pytest.raises(EmbedderError):
                emb.embed("text")

    def test_unexpected_response_shape(self):
        emb = OllamaEmbedder(host="http://x", model="frida")
        with patch("urllib.request.urlopen", return_value=_mock_response({"unexpected": "field"})):
            with pytest.raises(EmbedderError):
                emb.embed("text")


# ────────────────────────────────────────────────────────────────────────
# OpenAIEmbedder (LMStudio / llama.cpp / OpenAI proper)
# ────────────────────────────────────────────────────────────────────────


class TestOpenAIEmbedder:
    def test_basic_embedding_response(self):
        emb = OpenAIEmbedder(host="http://localhost:1234/v1", model="frida")
        payload = {"data": [{"embedding": [0.1, 0.2, 0.3], "index": 0}]}
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = emb.embed("text")
        assert result == [0.1, 0.2, 0.3]

    def test_endpoint_uses_v1_embeddings(self):
        emb = OpenAIEmbedder(host="http://localhost:1234/v1", model="frida")
        captured = {}

        def fake_urlopen(req, *args, **kwargs):
            captured["url"] = req.full_url
            captured["headers"] = {k.lower(): v for k, v in req.header_items()}
            return _mock_response({"data": [{"embedding": [0.0]}]})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            emb.embed("hello")
        assert captured["url"].endswith("/v1/embeddings")

    def test_api_key_in_authorization_header(self):
        emb = OpenAIEmbedder(host="http://x/v1", model="m", api_key="sk-test123")
        captured = {}

        def fake_urlopen(req, *args, **kwargs):
            captured["headers"] = {k.lower(): v for k, v in req.header_items()}
            return _mock_response({"data": [{"embedding": [0.1]}]})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            emb.embed("text")
        assert captured["headers"]["authorization"] == "Bearer sk-test123"

    def test_no_auth_header_when_no_key(self):
        emb = OpenAIEmbedder(host="http://x/v1", model="m", api_key=None)
        captured = {}

        def fake_urlopen(req, *args, **kwargs):
            captured["headers"] = {k.lower(): v for k, v in req.header_items()}
            return _mock_response({"data": [{"embedding": [0.1]}]})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            emb.embed("text")
        assert "authorization" not in captured["headers"]

    def test_url_error_raises_unavailable(self):
        emb = OpenAIEmbedder(host="http://nowhere/v1", model="m")
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            with pytest.raises(EmbedderUnavailable):
                emb.embed("text")

    def test_connection_error_raises_unavailable(self):
        emb = OpenAIEmbedder(host="http://x/v1", model="m")
        with patch("urllib.request.urlopen", side_effect=ConnectionError("boom")):
            with pytest.raises(EmbedderUnavailable):
                emb.embed("text")

    def test_http_error_raises_error(self):
        emb = OpenAIEmbedder(host="http://x/v1", model="m")
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("http://x/v1", 401, "Unauthorized", {}, None),
        ):
            with pytest.raises(EmbedderError):
                emb.embed("text")

    def test_unexpected_response_shape(self):
        emb = OpenAIEmbedder(host="http://x/v1", model="m")
        with patch("urllib.request.urlopen", return_value=_mock_response({"oops": True})):
            with pytest.raises(EmbedderError):
                emb.embed("text")

    def test_empty_data_array(self):
        emb = OpenAIEmbedder(host="http://x/v1", model="m")
        with patch("urllib.request.urlopen", return_value=_mock_response({"data": []})):
            with pytest.raises(EmbedderError):
                emb.embed("text")

    def test_host_trailing_slash_stripped(self):
        emb = OpenAIEmbedder(host="http://x/v1/", model="m")
        assert emb.host == "http://x/v1"


# ────────────────────────────────────────────────────────────────────────
# Graceful degradation in update_index
# ────────────────────────────────────────────────────────────────────────


class TestGracefulDegradation:
    def test_embedder_failure_propagates(self, tmp_path):
        # update_index doesn't swallow — callers (CLI / lint) handle
        idx = EmbedIndex(tmp_path / "x.json")
        with pytest.raises(EmbedderUnavailable):
            update_index(idx, [("A", "a")], FailingEmbedder())

    def test_partial_index_preserved_on_failure(self, tmp_path):
        # If embedder works for first page then fails, we keep what we got
        class PartialEmbedder(Embedder):
            def __init__(self):
                self.n = 0

            def embed(self, text: str) -> list[float]:
                self.n += 1
                if self.n == 1:
                    return [0.1, 0.2]
                raise EmbedderUnavailable("died")

        idx = EmbedIndex(tmp_path / "x.json")
        with pytest.raises(EmbedderUnavailable):
            update_index(idx, [("A", "a"), ("B", "b")], PartialEmbedder())
        assert "A" in idx.items
        assert "B" not in idx.items


# ────────────────────────────────────────────────────────────────────────
# Page discovery
# ────────────────────────────────────────────────────────────────────────


class TestDiscoverWikiPages:
    def test_walks_md_files(self, tmp_path, monkeypatch):
        wiki = tmp_path / "wiki"
        (wiki / "ideas").mkdir(parents=True)
        (wiki / "ideas" / "RLHF.md").write_text("# RLHF")
        (wiki / "index.md").write_text("# Index")
        monkeypatch.setattr(E, "WIKI_ROOT", wiki)
        names = {n for n, _ in discover_wiki_pages()}
        assert names == {"RLHF", "index"}

    def test_skips_lint_reports(self, tmp_path, monkeypatch):
        wiki = tmp_path / "wiki"
        meta = wiki / "meta"
        meta.mkdir(parents=True)
        (meta / "lint-report-2026-01-01.md").write_text("report")
        (meta / "real-meta.md").write_text("# meta")
        monkeypatch.setattr(E, "WIKI_ROOT", wiki)
        names = {n for n, _ in discover_wiki_pages()}
        assert "lint-report-2026-01-01" not in names
        assert "real-meta" in names

    def test_skips_subdir_artifacts(self, tmp_path, monkeypatch):
        # Current layout: wiki/meta/lint-reports/ and wiki/meta/snapshots/.
        # Legacy wiki/meta/kn-maps/ stays excluded for historical files.
        wiki = tmp_path / "wiki"
        (wiki / "meta" / "lint-reports").mkdir(parents=True)
        (wiki / "meta" / "snapshots").mkdir(parents=True)
        (wiki / "meta" / "kn-maps").mkdir(parents=True)
        (wiki / "meta" / "lint-reports" / "lint-report-2026-05.md").write_text("r")
        (wiki / "meta" / "snapshots" / "snapshot-2026-05.md").write_text("s")
        (wiki / "meta" / "kn-maps" / "knowledge-map-2026-05.md").write_text("k")
        (wiki / "meta" / "real-meta.md").write_text("m")
        monkeypatch.setattr(E, "WIKI_ROOT", wiki)
        names = {n for n, _ in discover_wiki_pages()}
        assert "lint-report-2026-05" not in names
        assert "snapshot-2026-05" not in names
        assert "knowledge-map-2026-05" not in names
        assert "real-meta" in names

    def test_no_wiki_dir_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(E, "WIKI_ROOT", tmp_path / "nonexistent")
        assert discover_wiki_pages() == []


class TestDiscoverRawPages:
    def test_skips_formats_dir(self, tmp_path, monkeypatch):
        raw = tmp_path / "raw"
        formats = raw / "formats"
        formats.mkdir(parents=True)
        (raw / "RLHF.md").write_text("notes")
        (formats / "paper.pdf.md").write_text("transcript-mock")
        monkeypatch.setattr(E, "RAW_ROOT", raw)
        names = {n for n, _ in discover_raw_pages()}
        assert "RLHF.md" in names
        assert not any("formats/" in n for n in names)

    def test_skips_meta_dir(self, tmp_path, monkeypatch):
        raw = tmp_path / "raw"
        meta = raw / "meta"
        meta.mkdir(parents=True)
        (raw / "good.md").write_text("body")
        (meta / "ingested.md").write_text("manifest")
        monkeypatch.setattr(E, "RAW_ROOT", raw)
        names = {n for n, _ in discover_raw_pages()}
        assert "good.md" in names
        assert not any("meta/" in n for n in names)

    def test_uses_relpath_as_key(self, tmp_path, monkeypatch):
        raw = tmp_path / "raw"
        articles = raw / "articles"
        articles.mkdir(parents=True)
        (articles / "post.md").write_text("body")
        monkeypatch.setattr(E, "RAW_ROOT", raw)
        names = {n for n, _ in discover_raw_pages()}
        assert "articles/post.md" in names

    def test_no_raw_dir_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(E, "RAW_ROOT", tmp_path / "nonexistent")
        assert discover_raw_pages() == []


# ────────────────────────────────────────────────────────────────────────
# wiki_page_paths
# ────────────────────────────────────────────────────────────────────────


class TestWikiPagePaths:
    def test_maps_name_to_path_and_folder(self, tmp_path, monkeypatch):
        wiki = tmp_path / "wiki"
        ideas = wiki / "ideas"
        ideas.mkdir(parents=True)
        (ideas / "RLHF.md").write_text("body")
        (wiki / "index.md").write_text("# index")
        monkeypatch.setattr(E, "WIKI_ROOT", wiki)

        paths = wiki_page_paths()
        assert "RLHF" in paths
        path, folder = paths["RLHF"]
        assert path.endswith("ideas/RLHF.md")
        assert folder == "ideas"

        # wiki root file → folder == ""
        path, folder = paths["index"]
        assert folder == ""

    def test_skips_lint_reports(self, tmp_path, monkeypatch):
        wiki = tmp_path / "wiki"
        meta = wiki / "meta"
        meta.mkdir(parents=True)
        (meta / "lint-report-2026-01-01.md").write_text("report")
        (meta / "real-meta.md").write_text("meta")
        monkeypatch.setattr(E, "WIKI_ROOT", wiki)

        paths = wiki_page_paths()
        assert "lint-report-2026-01-01" not in paths
        assert "real-meta" in paths

    def test_skips_subdir_artifacts(self, tmp_path, monkeypatch):
        # Current + legacy subdir artifacts must be skipped.
        wiki = tmp_path / "wiki"
        (wiki / "meta" / "lint-reports").mkdir(parents=True)
        (wiki / "meta" / "snapshots").mkdir(parents=True)
        (wiki / "meta" / "kn-maps").mkdir(parents=True)
        (wiki / "meta" / "lint-reports" / "lint-report-2026-05.md").write_text("r")
        (wiki / "meta" / "snapshots" / "snapshot-2026-05.md").write_text("s")
        (wiki / "meta" / "kn-maps" / "knowledge-map-2026-05.md").write_text("k")
        (wiki / "meta" / "real-meta.md").write_text("m")
        monkeypatch.setattr(E, "WIKI_ROOT", wiki)
        paths = wiki_page_paths()
        assert "lint-report-2026-05" not in paths
        assert "snapshot-2026-05" not in paths
        assert "knowledge-map-2026-05" not in paths
        assert "real-meta" in paths

    def test_no_wiki_dir_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(E, "WIKI_ROOT", tmp_path / "nonexistent")
        assert wiki_page_paths() == {}


# ────────────────────────────────────────────────────────────────────────
# _filter_and_format_results
# ────────────────────────────────────────────────────────────────────────


class TestFilterAndFormatResults:
    def test_filters_meta_folder(self):
        paths = {
            "RLHF": ("wiki/ideas/RLHF.md", "ideas"),
            "dashboard": ("wiki/meta/dashboard.md", "meta"),
        }
        results = [("dashboard", 0.9), ("RLHF", 0.8)]
        out = _filter_and_format_results(results, paths, k=5)
        names = [t[0] for t in out]
        assert "dashboard" not in names
        assert "RLHF" in names

    def test_filters_root_files(self):
        paths = {
            "RLHF": ("wiki/ideas/RLHF.md", "ideas"),
            "cache": ("wiki/cache.md", ""),  # root file
            "summary": ("wiki/summary.md", ""),
        }
        results = [("cache", 0.95), ("summary", 0.9), ("RLHF", 0.7)]
        out = _filter_and_format_results(results, paths, k=5)
        names = [t[0] for t in out]
        assert names == ["RLHF"]

    def test_include_meta_keeps_them(self):
        paths = {
            "RLHF": ("wiki/ideas/RLHF.md", "ideas"),
            "cache": ("wiki/cache.md", ""),
        }
        results = [("cache", 0.95), ("RLHF", 0.7)]
        out = _filter_and_format_results(results, paths, k=5, include_meta=True)
        names = [t[0] for t in out]
        assert names == ["cache", "RLHF"]

    def test_drops_stale_embeddings_silently(self):
        # Embedding for a page that no longer exists in wiki/
        paths = {"RLHF": ("wiki/ideas/RLHF.md", "ideas")}
        results = [("Deleted-Page", 0.9), ("RLHF", 0.8)]
        out = _filter_and_format_results(results, paths, k=5)
        names = [t[0] for t in out]
        assert "Deleted-Page" not in names
        assert "RLHF" in names

    def test_respects_k_limit(self):
        paths = {f"P{i}": (f"wiki/ideas/P{i}.md", "ideas") for i in range(10)}
        results = [(f"P{i}", 1.0 - i * 0.01) for i in range(10)]
        out = _filter_and_format_results(results, paths, k=3)
        assert len(out) == 3

    def test_attaches_full_path(self):
        paths = {"X": ("wiki/ideas/X.md", "ideas")}
        results = [("X", 0.5)]
        out = _filter_and_format_results(results, paths, k=1)
        _name, _sim, path, _summary = out[0]
        assert path == "wiki/ideas/X.md"

    def test_attaches_summary_when_present(self):
        paths = {"X": ("wiki/ideas/X.md", "ideas")}
        summaries = {"X": "key idea about X"}
        results = [("X", 0.5)]
        out = _filter_and_format_results(results, paths, k=1, summaries=summaries)
        _name, _sim, _path, summary = out[0]
        assert summary == "key idea about X"

    def test_empty_summary_when_absent(self):
        paths = {"X": ("wiki/ideas/X.md", "ideas")}
        results = [("X", 0.5)]
        # No summaries dict → empty string
        out = _filter_and_format_results(results, paths, k=1)
        _name, _sim, _path, summary = out[0]
        assert summary == ""

    def test_summary_lookup_misses_safely(self):
        # Page in paths but not in summaries → empty summary
        paths = {"X": ("wiki/ideas/X.md", "ideas")}
        summaries = {"OtherPage": "something"}
        results = [("X", 0.5)]
        out = _filter_and_format_results(results, paths, k=1, summaries=summaries)
        _, _, _, summary = out[0]
        assert summary == ""


# ────────────────────────────────────────────────────────────────────────
# parse_index_summaries
# ────────────────────────────────────────────────────────────────────────


class TestParseIndexSummaries:
    def test_basic_table(self, tmp_path):
        index = tmp_path / "index.md"
        index.write_text(
            "## Ideas\n"
            "| Страница | Суть |\n"
            "|---|---|\n"
            "| [[RLHF]] | RL from human feedback |\n"
            "| [[PPO]] | Proximal Policy Optimization |\n"
        )
        result = parse_index_summaries(index)
        assert result["RLHF"] == "RL from human feedback"
        assert result["PPO"] == "Proximal Policy Optimization"

    def test_multiple_sections(self, tmp_path):
        index = tmp_path / "index.md"
        index.write_text(
            "## Ideas\n"
            "| [[Idea1]] | first idea |\n"
            "## Entities\n"
            "| [[Person1]] | a person |\n"
            "## Domains\n"
            "| [[Domain1]] | a domain |\n"
        )
        result = parse_index_summaries(index)
        assert result == {
            "Idea1": "first idea",
            "Person1": "a person",
            "Domain1": "a domain",
        }

    def test_unknown_sections_ignored(self, tmp_path):
        index = tmp_path / "index.md"
        index.write_text(
            "## Changelog\n"
            "| [[Foo]] | bar |\n"  # Changelog isn't in known sections
            "## Ideas\n"
            "| [[Real]] | real summary |\n"
        )
        result = parse_index_summaries(index)
        assert "Foo" not in result
        assert result["Real"] == "real summary"

    def test_separator_row_skipped(self, tmp_path):
        index = tmp_path / "index.md"
        index.write_text(
            "## Ideas\n"
            "| Страница | Суть |\n"
            "|---|---|\n"
            "| [[Real]] | real |\n"
        )
        result = parse_index_summaries(index)
        # Only "Real", not the header row
        assert result == {"Real": "real"}

    def test_aliased_wikilink(self, tmp_path):
        # [[Page|Alias]] should still extract Page as the key
        index = tmp_path / "index.md"
        index.write_text(
            "## Ideas\n"
            "| [[Real-Name\\|displayed]] | summary |\n"
        )
        result = parse_index_summaries(index)
        assert "Real-Name" in result

    def test_empty_summary_skipped(self, tmp_path):
        # Row without summary text → skip
        index = tmp_path / "index.md"
        index.write_text(
            "## Ideas\n"
            "| [[A]] | has summary |\n"
            "| [[B]] |  |\n"
        )
        result = parse_index_summaries(index)
        assert "A" in result
        assert "B" not in result

    def test_frontmatter_stripped(self, tmp_path):
        index = tmp_path / "index.md"
        index.write_text(
            "---\n"
            "type: meta\n"
            "---\n"
            "## Ideas\n"
            "| [[X]] | content |\n"
        )
        result = parse_index_summaries(index)
        assert result == {"X": "content"}

    def test_missing_file(self, tmp_path):
        result = parse_index_summaries(tmp_path / "does-not-exist.md")
        assert result == {}

    def test_no_tables_returns_empty(self, tmp_path):
        index = tmp_path / "index.md"
        index.write_text("# Just a heading\n\nNothing structured.\n")
        assert parse_index_summaries(index) == {}


# ────────────────────────────────────────────────────────────────────────
# _format_result_line
# ────────────────────────────────────────────────────────────────────────


class TestFormatResultLine:
    def test_with_summary(self):
        line = _format_result_line(0.621, "wiki/ideas/RLHF.md", "RL from human feedback")
        assert "+0.621" in line
        assert "wiki/ideas/RLHF.md" in line
        assert "— RL from human feedback" in line

    def test_without_summary(self):
        line = _format_result_line(0.5, "wiki/ideas/X.md", "")
        assert "—" not in line
        assert "wiki/ideas/X.md" in line

    def test_negative_similarity(self):
        line = _format_result_line(-0.1, "wiki/ideas/X.md", "")
        assert "-0.100" in line
