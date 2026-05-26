"""Unit tests for bin/static_lint.py — Layer 1 static lint.

Coverage: registered checks + frontmatter parser + wikilink extractor.

Design notes:
- Every check function is pure (list[Page] → Iterable[Issue]) — no I/O for
  most checks. Tests build Page objects directly via make_page().
- Filesystem-dependent checks (binary-source) use pytest tmp_path +
  monkeypatch on module-level constants.
- "Clean" case + "violation" case + key edge cases for each check.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

import pytest

# Make `bin/` importable regardless of cwd
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import static_lint as L
from static_lint import (
    Issue, Page,
    _extract_wikilinks,
    _fix_folder_type_mismatch_in_text,
    _fix_inline_tags_in_text,
    _fix_invalid_fields_extra_in_text,
    _fix_invalid_fields_missing_in_text,
    _fix_non_canonical_wikilink_in_text,
    _fix_raw_link_with_extension_in_text,
    _fix_raw_ref_in_body_in_text,
    _fix_status_not_in_enum_in_text,
    _load_template_schemas,
    _normalize_wiki_target,
    _normalize_wikilink_text,
    _parse_yaml_subset,
    _resolve_templater,
    _wikilink_to_raw_key,
    apply_auto_fixes,
    compute_page_hashes,
    compute_touched_pages,
    issue_involves_pages,
    check_asymmetric_related,
    check_binary_source_outside_formats,
    check_dangling_domain_ref,
    check_dead_link,
    check_folder_type_mismatch,
    check_inline_tags,
    check_invalid_fields,
    check_missing_summary,
    check_non_canonical_wikilink,
    check_orphan,
    check_raw_link_with_extension,
    check_raw_ref_in_body,
    check_similar_but_unlinked,
    check_status_not_in_enum,
    check_synthesis_drift,
    compute_contradiction_candidates,
    compute_wiki_hash,
    parse_frontmatter,
)

# Make embed module importable in tests
import embed as E
from embed import EmbedIndex


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────


def make_page(
    folder: str = "ideas",
    name: str = "Test-Page",
    fm_yaml: str = "",
    body: str = "",
) -> Page:
    """Build a Page from raw YAML + body text without filesystem access."""
    text = f"---\n{fm_yaml}\n---\n{body}" if fm_yaml else body
    fm, parsed_body = parse_frontmatter(text)
    path = (
        Path(f"wiki/{folder}/{name}.md") if folder else Path(f"wiki/{name}.md")
    )
    return Page(
        path=path,
        folder=folder,
        name=name,
        text=text,
        fm=fm,
        body=parsed_body,
    )


def issues_of(result) -> list[Issue]:
    return list(result)


def types_of(result) -> list[str]:
    return [i.type for i in issues_of(result)]


# ────────────────────────────────────────────────────────────────────────
# Frontmatter parser
# ────────────────────────────────────────────────────────────────────────


class TestParseFrontmatter:
    def test_basic_fields(self):
        text = "---\ntype: idea\nstatus: ready\n---\nbody"
        fm, body = parse_frontmatter(text)
        assert fm is not None
        assert fm.fields["type"] == "idea"
        assert fm.fields["status"] == "ready"
        assert body.strip() == "body"

    def test_block_list(self):
        text = "---\ntags:\n  - ML\n  - RL\n---\n"
        fm, _ = parse_frontmatter(text)
        assert fm.fields["tags"] == ["ML", "RL"]

    def test_inline_list(self):
        text = "---\ntags: [ML, RL]\n---\n"
        fm, _ = parse_frontmatter(text)
        assert fm.fields["tags"] == ["ML", "RL"]
        assert "tags" in fm.inline_lists

    def test_quoted_wikilinks(self):
        text = '---\nrelated:\n  - "[[RLHF]]"\n---\n'
        fm, _ = parse_frontmatter(text)
        assert fm.fields["related"] == ["[[RLHF]]"]

    def test_no_frontmatter(self):
        text = "# Just a heading\n\nsome text"
        fm, body = parse_frontmatter(text)
        assert fm is None
        assert "heading" in body

    def test_empty_list(self):
        text = "---\ntags: []\n---\n"
        fm, _ = parse_frontmatter(text)
        assert fm.fields["tags"] == []

    def test_null_value(self):
        text = "---\ndomain:\n---\n"
        fm, _ = parse_frontmatter(text)
        assert fm.fields["domain"] is None


# ────────────────────────────────────────────────────────────────────────
# Wikilink extraction
# ────────────────────────────────────────────────────────────────────────


class TestNormalizeWikiTarget:
    def test_basename_unchanged(self):
        assert _normalize_wiki_target("RLHF") == "RLHF"

    def test_strips_wiki_prefix(self):
        assert _normalize_wiki_target("wiki/ideas/RLHF") == "RLHF"

    def test_strips_subfolder_prefix(self):
        assert _normalize_wiki_target("ideas/RLHF") == "RLHF"

    def test_raw_path_preserved(self):
        # raw/ has legitimate folder structure — never normalize
        assert _normalize_wiki_target("raw/articles/foo") == "raw/articles/foo"

    def test_raw_at_root_preserved(self):
        assert _normalize_wiki_target("raw/RLHF") == "raw/RLHF"

    def test_deep_path(self):
        assert _normalize_wiki_target("a/b/c/Page") == "Page"


class TestExtractWikilinks:
    def test_basic_link(self):
        assert _extract_wikilinks("See [[RLHF]] for details") == ["RLHF"]

    def test_aliased_link(self):
        assert _extract_wikilinks("[[RLHF|Reinforcement Learning]]") == ["RLHF"]

    def test_anchor_link(self):
        assert _extract_wikilinks("[[RLHF#section]]") == ["RLHF"]

    def test_embed_excluded(self):
        assert _extract_wikilinks("![[image.png]]") == []

    def test_fenced_code_block_stripped(self):
        text = "```\n[[should-be-ignored]]\n```\noutside"
        cleaned = _normalize_wikilink_text(text)
        links = _extract_wikilinks(cleaned)
        assert "should-be-ignored" not in links

    def test_inline_code_stripped(self):
        text = "Use `[[not-a-link]]` in code"
        cleaned = _normalize_wikilink_text(text)
        assert "not-a-link" not in _extract_wikilinks(cleaned)

    def test_escaped_pipe_in_table(self):
        # In markdown tables, | is escaped as \|
        text = r"[[Page\|Alias]]"
        links = _extract_wikilinks(text)
        # After normalization, the pipe is real — link should be extracted
        assert len(links) >= 1

    def test_multiple_links(self):
        links = _extract_wikilinks("[[A]] and [[B]] and [[C]]")
        assert set(links) == {"A", "B", "C"}

    def test_path_prefixed_normalized(self):
        # [[wiki/ideas/RLHF]] should normalize to "RLHF" in extracted list
        links = _extract_wikilinks("Reference: [[wiki/ideas/RLHF]] elsewhere")
        assert links == ["RLHF"]

    def test_raw_path_preserved(self):
        # raw/ targets keep their full path
        links = _extract_wikilinks("Source: [[raw/articles/foo]]")
        assert links == ["raw/articles/foo"]


# ────────────────────────────────────────────────────────────────────────
# check_non_canonical_wikilink
# ────────────────────────────────────────────────────────────────────────


class TestCheckNonCanonicalWikilink:
    def test_canonical_body_link_ok(self):
        p = make_page(fm_yaml="type: idea", body="See [[RLHF]] for details.\n")
        assert types_of(check_non_canonical_wikilink([p])) == []

    def test_path_prefixed_body_link_flagged(self):
        p = make_page(
            name="A", fm_yaml="type: idea",
            body="See [[wiki/ideas/RLHF]] for details.\n",
        )
        issues = issues_of(check_non_canonical_wikilink([p]))
        assert len(issues) == 1
        assert issues[0].type == "non-canonical-wikilink"
        assert issues[0].payload["link"] == "[[wiki/ideas/RLHF]]"
        assert issues[0].payload["fix"] == "[[RLHF]]"

    def test_raw_paths_not_flagged(self):
        # raw/ targets legitimately use path structure
        p = make_page(
            fm_yaml='type: idea\nsources:\n  - "[[raw/articles/foo]]"',
            body="From [[raw/articles/foo]].\n",
        )
        # Note: raw-ref-in-body fires here, but non-canonical-wikilink should NOT
        assert types_of(check_non_canonical_wikilink([p])) == []

    def test_frontmatter_related_flagged(self):
        p = make_page(
            fm_yaml='type: idea\nrelated:\n  - "[[wiki/ideas/PPO]]"',
        )
        issues = issues_of(check_non_canonical_wikilink([p]))
        assert len(issues) == 1
        assert issues[0].payload["context"] == "frontmatter related"
        assert issues[0].payload["fix"] == "[[PPO]]"

    def test_frontmatter_domain_flagged(self):
        p = make_page(
            fm_yaml='type: idea\ndomain:\n  - "[[domains/Machine Learning]]"',
        )
        issues = issues_of(check_non_canonical_wikilink([p]))
        assert any("domain" in i.payload["context"] for i in issues)

    def test_meta_pages_exempt(self):
        # Meta pages may legitimately mention path-prefixed links in operation logs
        p = make_page(
            folder="meta", fm_yaml="type: meta",
            body="Ingested [[wiki/ideas/RLHF]].\n",
        )
        assert types_of(check_non_canonical_wikilink([p])) == []

    def test_fenced_code_skipped(self):
        # Code examples may show path-prefixed links — don't flag them
        body = "```\nUse [[wiki/ideas/RLHF]] syntax\n```\n"
        p = make_page(fm_yaml="type: idea", body=body)
        assert types_of(check_non_canonical_wikilink([p])) == []

    def test_dedup_within_page(self):
        # Same link appearing twice in body → reported once
        body = "Some [[wiki/ideas/RLHF]]. Other [[wiki/ideas/RLHF]] mention.\n"
        p = make_page(fm_yaml="type: idea", body=body)
        issues = issues_of(check_non_canonical_wikilink([p]))
        assert len(issues) == 1

    def test_anchor_preserved_in_fix(self):
        body = "See [[wiki/ideas/RLHF#Section Title]] there.\n"
        p = make_page(fm_yaml="type: idea", body=body)
        issues = issues_of(check_non_canonical_wikilink([p]))
        assert len(issues) == 1
        assert issues[0].payload["fix"] == "[[RLHF#Section Title]]"

    def test_alias_preserved_in_fix(self):
        body = "See [[wiki/ideas/RLHF|кастомный текст]] there.\n"
        p = make_page(fm_yaml="type: idea", body=body)
        issues = issues_of(check_non_canonical_wikilink([p]))
        assert len(issues) == 1
        assert issues[0].payload["fix"] == "[[RLHF|кастомный текст]]"

    def test_anchor_and_alias_preserved(self):
        body = "See [[wiki/ideas/RLHF#Section|alias]] there.\n"
        p = make_page(fm_yaml="type: idea", body=body)
        issues = issues_of(check_non_canonical_wikilink([p]))
        assert len(issues) == 1
        assert issues[0].payload["fix"] == "[[RLHF#Section|alias]]"


# ────────────────────────────────────────────────────────────────────────
# Integration: normalization eliminates false dead-links / index issues
# ────────────────────────────────────────────────────────────────────────


class TestNormalizationIntegration:
    """Path-prefixed wikilinks should NOT trigger false positives in
    other checks once normalization is applied."""

    def test_path_prefixed_does_not_cause_dead_link(self):
        # Page A links to B via path-prefixed form. Both pages exist.
        # check_dead_link should resolve [[wiki/ideas/B]] → "B" → in by_name.
        a = make_page(name="A", fm_yaml="type: idea", body="See [[wiki/ideas/B]].\n")
        b = make_page(name="B", fm_yaml="type: idea")
        assert types_of(check_dead_link([a, b])) == []

    def test_path_prefixed_in_related_symmetric(self):
        # A.related has [[wiki/ideas/B]]; B.related has [[A]]. Should be symmetric.
        a = make_page(name="A", fm_yaml='type: idea\nrelated:\n  - "[[wiki/ideas/B]]"')
        b = make_page(name="B", fm_yaml='type: idea\nrelated:\n  - "[[A]]"')
        assert types_of(check_asymmetric_related([a, b])) == []


# ────────────────────────────────────────────────────────────────────────
# check_status_not_in_enum
# ────────────────────────────────────────────────────────────────────────


class TestCheckStatusNotInEnum:
    def test_valid_statuses_ok(self):
        for s in ("evaluation", "in-progress", "ready"):
            p = make_page(fm_yaml=f"type: idea\nstatus: {s}")
            assert types_of(check_status_not_in_enum([p])) == []

    def test_invalid_status_flagged(self):
        p = make_page(fm_yaml="type: idea\nstatus: stable")
        issues = issues_of(check_status_not_in_enum([p]))
        assert len(issues) == 1
        assert issues[0].type == "status-not-in-enum"
        assert issues[0].payload["value"] == "stable"

    def test_entity_skipped(self):
        # entity status is handled by check_invalid_fields (extra field) instead
        p = make_page(folder="entities", fm_yaml="type: entity\nstatus: stable")
        assert types_of(check_status_not_in_enum([p])) == []

    def test_no_status_field_ok(self):
        p = make_page(fm_yaml="type: idea")
        assert types_of(check_status_not_in_enum([p])) == []

    def test_fix_suggestion_present(self):
        p = make_page(fm_yaml="type: idea\nstatus: done")
        issues = issues_of(check_status_not_in_enum([p]))
        assert "fix" in issues[0].payload

    def test_mind_draft_ok(self):
        p = make_page(folder="minds", fm_yaml="type: mind\nstatus: draft")
        assert types_of(check_status_not_in_enum([p])) == []

    def test_mind_stable_ok(self):
        p = make_page(folder="minds", fm_yaml="type: mind\nstatus: stable")
        assert types_of(check_status_not_in_enum([p])) == []

    def test_mind_deprecated_ok(self):
        p = make_page(folder="minds", fm_yaml="type: mind\nstatus: deprecated")
        assert types_of(check_status_not_in_enum([p])) == []

    def test_mind_in_progress_flagged(self):
        # 'in-progress' is valid for ideas but NOT for mind — different enum
        p = make_page(folder="minds", fm_yaml="type: mind\nstatus: in-progress")
        issues = issues_of(check_status_not_in_enum([p]))
        assert len(issues) == 1
        assert issues[0].type == "status-not-in-enum"
        assert issues[0].payload["value"] == "in-progress"

    def test_mind_fix_suggestion_is_draft(self):
        # default fix for mind is "draft", not "in-progress"
        p = make_page(folder="minds", fm_yaml="type: mind\nstatus: ready")
        issues = issues_of(check_status_not_in_enum([p]))
        assert issues[0].payload["fix"] == "draft"


# ────────────────────────────────────────────────────────────────────────
# _load_template_field_names
# ────────────────────────────────────────────────────────────────────────


class TestLoadTemplateSchemas:
    def test_loads_all_templates(self, tmp_path, monkeypatch):
        templates = tmp_path / "_templates"
        templates.mkdir()
        (templates / "idea.md").write_text(
            "---\ntype: idea\nstatus: evaluation\ntags: []\n---\n"
        )
        (templates / "entity.md").write_text(
            "---\ntype: entity\nentity_type: person\nrole: ''\n---\n"
        )
        monkeypatch.setattr(L, "TEMPLATES_DIR", templates)
        result = _load_template_schemas()
        assert set(result["idea"].keys()) == {"type", "status", "tags"}
        assert set(result["entity"].keys()) == {"type", "entity_type", "role"}

    def test_preserves_raw_entries(self, tmp_path, monkeypatch):
        # Raw values matter for missing-field auto-fix
        templates = tmp_path / "_templates"
        templates.mkdir()
        (templates / "idea.md").write_text(
            "---\ntype: idea\nstatus: evaluation\ntags: []\nsummary: ''\n---\n"
        )
        monkeypatch.setattr(L, "TEMPLATES_DIR", templates)
        result = _load_template_schemas()
        assert result["idea"]["status"] == "status: evaluation"
        assert result["idea"]["tags"] == "tags: []"
        assert result["idea"]["summary"] == "summary: ''"

    def test_preserves_multiline_block_lists(self, tmp_path, monkeypatch):
        templates = tmp_path / "_templates"
        templates.mkdir()
        (templates / "domain.md").write_text(
            "---\ntype: domain\ndomain:\n  - \"[[X]]\"\n  - \"[[Y]]\"\ntags: []\n---\n"
        )
        monkeypatch.setattr(L, "TEMPLATES_DIR", templates)
        result = _load_template_schemas()
        assert result["domain"]["domain"] == 'domain:\n  - "[[X]]"\n  - "[[Y]]"'
        assert result["domain"]["tags"] == "tags: []"

    def test_missing_directory_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(L, "TEMPLATES_DIR", tmp_path / "nonexistent")
        assert _load_template_schemas() == {}

    def test_template_without_frontmatter_skipped(self, tmp_path, monkeypatch):
        templates = tmp_path / "_templates"
        templates.mkdir()
        (templates / "broken.md").write_text("just text, no frontmatter")
        (templates / "idea.md").write_text("---\ntype: idea\n---\n")
        monkeypatch.setattr(L, "TEMPLATES_DIR", templates)
        result = _load_template_schemas()
        assert "broken" not in result
        assert "idea" in result


# ────────────────────────────────────────────────────────────────────────
# check_invalid_fields
# ────────────────────────────────────────────────────────────────────────


@pytest.fixture
def invalid_fields_schemas(tmp_path, monkeypatch):
    """Set up minimal _templates/ for invalid-fields check."""
    templates = tmp_path / "_templates"
    templates.mkdir()
    (templates / "idea.md").write_text(
        "---\ntype: idea\nsummary: ''\nstatus: evaluation\n"
        "tags: []\nrelated: []\nsources: []\n---\n"
    )
    (templates / "entity.md").write_text(
        "---\ntype: entity\nsummary: ''\nentity_type: person\n"
        "role: ''\ntags: []\n---\n"
    )
    monkeypatch.setattr(L, "TEMPLATES_DIR", templates)


class TestCheckInvalidFields:
    def test_clean_page_no_issues(self, invalid_fields_schemas):
        p = make_page(fm_yaml=(
            "type: idea\nsummary: 'x'\nstatus: ready\n"
            "tags: []\nrelated: []\nsources: []"
        ))
        assert types_of(check_invalid_fields([p])) == []

    def test_extra_field_flagged(self, invalid_fields_schemas):
        p = make_page(fm_yaml=(
            "type: idea\nsummary: 'x'\nstatus: ready\n"
            "tags: []\nrelated: []\nsources: []\nfoobar: x"
        ))
        issues = issues_of(check_invalid_fields([p]))
        assert len(issues) == 1
        assert issues[0].type == "invalid-fields"
        assert issues[0].payload["subtype"] == "extra"
        assert issues[0].payload["field"] == "foobar"

    def test_status_on_entity_caught(self, invalid_fields_schemas):
        # Previously a separate check (status-on-entity), now subsumed
        p = make_page(folder="entities", fm_yaml=(
            "type: entity\nsummary: 'x'\nentity_type: person\n"
            "role: 'r'\ntags: []\nstatus: ready"
        ))
        issues = issues_of(check_invalid_fields([p]))
        assert any(
            i.payload["subtype"] == "extra" and i.payload["field"] == "status"
            for i in issues
        )

    def test_legacy_field_caught(self, invalid_fields_schemas):
        # Previously check_legacy_field for title/complexity/first_mentioned
        p = make_page(fm_yaml=(
            "type: idea\nsummary: 'x'\nstatus: ready\n"
            "tags: []\nrelated: []\nsources: []\ntitle: 'Old'"
        ))
        issues = issues_of(check_invalid_fields([p]))
        assert any(
            i.payload["subtype"] == "extra" and i.payload["field"] == "title"
            for i in issues
        )

    def test_missing_field_flagged(self, invalid_fields_schemas):
        p = make_page(fm_yaml=(
            # missing related and sources
            "type: idea\nsummary: 'x'\nstatus: ready\ntags: []"
        ))
        issues = issues_of(check_invalid_fields([p]))
        missing = {
            i.payload["field"] for i in issues
            if i.payload["subtype"] == "missing"
        }
        assert missing == {"related", "sources"}

    def test_missing_summary_skipped(self, invalid_fields_schemas):
        # summary missing → handled by check_missing_summary, not invalid-fields
        p = make_page(fm_yaml=(
            # missing summary
            "type: idea\nstatus: ready\ntags: []\nrelated: []\nsources: []"
        ))
        issues = issues_of(check_invalid_fields([p]))
        for i in issues:
            assert not (
                i.payload["subtype"] == "missing"
                and i.payload["field"] == "summary"
            )

    def test_multiple_extras_flagged_separately(self, invalid_fields_schemas):
        p = make_page(fm_yaml=(
            "type: idea\nsummary: 'x'\nstatus: ready\n"
            "tags: []\nrelated: []\nsources: []\nfoo: 1\nbar: 2"
        ))
        issues = issues_of(check_invalid_fields([p]))
        extras = {
            i.payload["field"] for i in issues
            if i.payload["subtype"] == "extra"
        }
        assert extras == {"foo", "bar"}

    def test_meta_page_exempt(self, invalid_fields_schemas):
        p = make_page(folder="meta", fm_yaml="type: meta\nfoo: bar")
        assert types_of(check_invalid_fields([p])) == []

    def test_unknown_type_exempt(self, invalid_fields_schemas):
        # No template for "unknown" → no check (graceful degradation)
        p = make_page(fm_yaml="type: unknown\nfoo: bar")
        assert types_of(check_invalid_fields([p])) == []

    def test_no_template_directory_no_crash(self, tmp_path, monkeypatch):
        monkeypatch.setattr(L, "TEMPLATES_DIR", tmp_path / "nope")
        p = make_page(fm_yaml="type: idea\nfoo: bar")
        # No template loaded → no issues (degrade gracefully)
        assert types_of(check_invalid_fields([p])) == []

    def test_no_frontmatter_no_crash(self, invalid_fields_schemas):
        p = make_page(fm_yaml="", body="just body")
        assert types_of(check_invalid_fields([p])) == []


# ────────────────────────────────────────────────────────────────────────
# check_inline_tags
# ────────────────────────────────────────────────────────────────────────


class TestCheckInlineTags:
    def test_block_style_ok(self):
        p = make_page(fm_yaml="type: idea\ntags:\n  - ML")
        assert types_of(check_inline_tags([p])) == []

    def test_empty_inline_ok(self):
        # tags: [] is a valid placeholder
        p = make_page(fm_yaml="type: idea\ntags: []")
        assert types_of(check_inline_tags([p])) == []

    def test_non_empty_inline_flagged(self):
        p = make_page(fm_yaml="type: idea\ntags: [ML, RL]")
        issues = issues_of(check_inline_tags([p]))
        assert len(issues) == 1
        assert issues[0].type == "inline-tags"


# ────────────────────────────────────────────────────────────────────────
# check_folder_type_mismatch
# ────────────────────────────────────────────────────────────────────────


class TestCheckFolderTypeMismatch:
    def test_matching_ok(self):
        p = make_page(folder="ideas", fm_yaml="type: idea")
        assert types_of(check_folder_type_mismatch([p])) == []

    def test_mismatch_flagged(self):
        p = make_page(folder="ideas", fm_yaml="type: entity")
        issues = issues_of(check_folder_type_mismatch([p]))
        assert len(issues) == 1
        assert issues[0].payload["expected_type"] == "idea"
        assert issues[0].payload["current_type"] == "entity"

    def test_meta_folder_exempt(self):
        p = make_page(folder="meta", fm_yaml="type: meta")
        assert types_of(check_folder_type_mismatch([p])) == []

    def test_wiki_root_exempt(self):
        p = make_page(folder="", name="index", fm_yaml="type: meta")
        assert types_of(check_folder_type_mismatch([p])) == []


# ────────────────────────────────────────────────────────────────────────
# check_raw_link_with_extension
# ────────────────────────────────────────────────────────────────────────


class TestCheckRawLinkWithExtension:
    def test_clean_link_ok(self):
        p = make_page(fm_yaml='type: idea\nsources:\n  - "[[raw/paper]]"')
        assert types_of(check_raw_link_with_extension([p])) == []

    def test_md_extension_flagged(self):
        p = make_page(fm_yaml='type: idea\nsources:\n  - "[[raw/paper.md]]"')
        issues = issues_of(check_raw_link_with_extension([p]))
        assert len(issues) == 1
        assert issues[0].type == "raw-link-with-extension"

    def test_compound_extension_ok(self):
        # raw/paper.docx.md — .md is necessary to distinguish from original
        p = make_page(fm_yaml='type: idea\nsources:\n  - "[[raw/paper.docx.md]]"')
        assert types_of(check_raw_link_with_extension([p])) == []

    def test_no_sources_ok(self):
        p = make_page(fm_yaml="type: idea")
        assert types_of(check_raw_link_with_extension([p])) == []


# ────────────────────────────────────────────────────────────────────────
# check_raw_ref_in_body
# ────────────────────────────────────────────────────────────────────────


class TestCheckRawRefInBody:
    def test_no_raw_refs_ok(self):
        p = make_page(fm_yaml="type: idea", body="See [[RLHF]] for context.\n")
        assert types_of(check_raw_ref_in_body([p])) == []

    def test_raw_ref_in_body_flagged(self):
        p = make_page(
            fm_yaml="type: idea",
            body="Based on [[raw/RLHF]] notes.\n",
        )
        issues = issues_of(check_raw_ref_in_body([p]))
        assert len(issues) == 1
        assert issues[0].type == "raw-ref-in-body"

    def test_meta_page_exempt(self):
        p = make_page(
            folder="meta",
            fm_yaml="type: meta",
            body="Ingested [[raw/paper]] at 10:00.\n",
        )
        assert types_of(check_raw_ref_in_body([p])) == []


# ────────────────────────────────────────────────────────────────────────
# check_dead_link
# ────────────────────────────────────────────────────────────────────────


class TestCheckDeadLink:
    def _two_pages(self, body_a: str = "", body_b: str = ""):
        a = make_page(name="Page-A", fm_yaml="type: idea", body=body_a)
        b = make_page(name="Page-B", fm_yaml="type: idea", body=body_b)
        return [a, b]

    def test_existing_link_ok(self):
        pages = self._two_pages(body_a="See [[Page-B]].\n")
        assert types_of(check_dead_link(pages)) == []

    def test_missing_link_flagged(self):
        pages = self._two_pages(body_a="See [[Ghost-Page]].\n")
        issues = issues_of(check_dead_link(pages))
        assert len(issues) == 1
        assert issues[0].type == "dead-link"
        assert "Ghost-Page" in issues[0].payload["what"]

    def test_link_inside_fenced_code_ignored(self):
        body = "```\nSee [[Ghost-Page]].\n```\n"
        pages = self._two_pages(body_a=body)
        assert types_of(check_dead_link(pages)) == []

    def test_link_inside_inline_code_ignored(self):
        body = "Use `[[Ghost-Page]]` syntax.\n"
        pages = self._two_pages(body_a=body)
        assert types_of(check_dead_link(pages)) == []

    def test_raw_links_ignored(self):
        pages = self._two_pages(body_a="See [[raw/paper]].\n")
        # raw links are exempt from dead-link check
        assert types_of(check_dead_link(pages)) == []

    def test_dead_link_in_frontmatter_related(self):
        p = make_page(
            fm_yaml='type: idea\nrelated:\n  - "[[NonExistent]]"',
            body="",
        )
        issues = issues_of(check_dead_link([p]))
        assert any(i.type == "dead-link" for i in issues)

    def test_meta_pages_exempt(self):
        p = make_page(
            folder="meta",
            fm_yaml="type: meta",
            body="See [[Ghost-Page]] in old report.\n",
        )
        assert types_of(check_dead_link([p])) == []

    def test_deduplicated_per_page_target(self):
        body = "[[Ghost]] appears here and [[Ghost]] again.\n"
        pages = self._two_pages(body_a=body)
        issues = issues_of(check_dead_link(pages))
        # Only one issue per (page, target) pair
        assert len(issues) == 1


# ────────────────────────────────────────────────────────────────────────
# check_orphan
# ────────────────────────────────────────────────────────────────────────


class TestCheckOrphan:
    def test_linked_page_ok(self):
        # A links to B → B has an inbound link and should NOT be orphan.
        # A itself has no inbound links, so it IS an orphan — that's correct.
        a = make_page(name="A", fm_yaml="type: idea", body="See [[B]].\n")
        b = make_page(name="B", fm_yaml="type: idea")
        orphan_pages = [i.payload["where"] for i in check_orphan([a, b])]
        assert not any("Page-B" in w or w.endswith("/B.md") for w in orphan_pages)

    def test_unlinked_page_flagged(self):
        p = make_page(name="Lonely", fm_yaml="type: idea")
        issues = issues_of(check_orphan([p]))
        assert any(i.type == "orphan" for i in issues)

    def test_meta_page_exempt(self):
        p = make_page(folder="meta", name="cache", fm_yaml="type: meta")
        assert types_of(check_orphan([p])) == []

    def test_wiki_root_file_exempt(self):
        p = make_page(folder="", name="index", fm_yaml="type: meta")
        assert types_of(check_orphan([p])) == []

    def test_frontmatter_related_counts_as_link(self):
        # A's `related:` references B — B has inbound link and must not be orphan.
        a = make_page(
            name="A",
            fm_yaml='type: idea\nrelated:\n  - "[[B]]"',
        )
        b = make_page(name="B", fm_yaml="type: idea")
        orphan_pages = [i.payload["where"] for i in check_orphan([a, b])]
        assert not any(w.endswith("/B.md") for w in orphan_pages)

    def test_deprecated_page_exempt(self):
        # status: deprecated pages aren't expected to have inbound links —
        # they've been replaced by a newer revision (supersedes flow in brainstorm).
        p = make_page(
            name="Old-Mind",
            folder="minds",
            fm_yaml="type: mind\nstatus: deprecated",
        )
        assert types_of(check_orphan([p])) == []


# ────────────────────────────────────────────────────────────────────────
# check_asymmetric_related
# ────────────────────────────────────────────────────────────────────────


class TestCheckAsymmetricRelated:
    def test_symmetric_ok(self):
        a = make_page(name="A", fm_yaml='type: idea\nrelated:\n  - "[[B]]"')
        b = make_page(name="B", fm_yaml='type: idea\nrelated:\n  - "[[A]]"')
        assert types_of(check_asymmetric_related([a, b])) == []

    def test_asymmetric_flagged(self):
        a = make_page(name="A", fm_yaml='type: idea\nrelated:\n  - "[[B]]"')
        b = make_page(name="B", fm_yaml="type: idea\nrelated: []")
        issues = issues_of(check_asymmetric_related([a, b]))
        assert len(issues) == 1
        assert issues[0].type == "asymmetric-related"

    def test_reported_once_per_pair(self):
        # A→B and B→ nothing: only one issue, not two
        a = make_page(name="A", fm_yaml='type: idea\nrelated:\n  - "[[B]]"')
        b = make_page(name="B", fm_yaml="type: idea")
        assert len(issues_of(check_asymmetric_related([a, b]))) == 1

    def test_dead_related_link_not_double_reported(self):
        # A→Ghost: dead-link check handles this; asymmetric should skip missing pages
        a = make_page(name="A", fm_yaml='type: idea\nrelated:\n  - "[[Ghost]]"')
        issues = issues_of(check_asymmetric_related([a]))
        # Ghost doesn't exist in by_name → continue (no asymmetric issue)
        assert types_of(issues_of(check_asymmetric_related([a]))) == []

    def test_mind_to_idea_asymmetric_ok(self):
        # mind→idea is a one-way reference by design. The encyclopedia idea
        # shouldn't carry backlinks to every author note that mentions it —
        # Obsidian's backlinks pane already surfaces them at the UI level.
        m = make_page(
            name="MyMind",
            folder="minds",
            fm_yaml='type: mind\nrelated:\n  - "[[X]]"',
        )
        x = make_page(name="X", fm_yaml="type: idea\nrelated: []")
        assert types_of(check_asymmetric_related([m, x])) == []

    def test_idea_to_mind_asymmetric_ok(self):
        # Same skip applies if the mind is page_b — direction shouldn't matter.
        m = make_page(name="MyMind", folder="minds", fm_yaml="type: mind\nrelated: []")
        x = make_page(name="X", fm_yaml='type: idea\nrelated:\n  - "[[MyMind]]"')
        assert types_of(check_asymmetric_related([x, m])) == []


# ────────────────────────────────────────────────────────────────────────
# check_dangling_domain_ref
# ────────────────────────────────────────────────────────────────────────


class TestCheckDanglingDomainRef:
    def test_existing_domain_ok(self):
        domain = make_page(folder="domains", name="Machine-Learning", fm_yaml="type: domain")
        idea = make_page(
            fm_yaml='type: idea\ndomain:\n  - "[[Machine-Learning]]"'
        )
        assert types_of(check_dangling_domain_ref([domain, idea])) == []

    def test_missing_domain_flagged(self):
        idea = make_page(
            fm_yaml='type: idea\ndomain:\n  - "[[NonExistent-Domain]]"'
        )
        issues = issues_of(check_dangling_domain_ref([idea]))
        assert len(issues) == 1
        assert issues[0].type == "dangling-domain-ref"
        assert issues[0].payload["missing_domain"] == "NonExistent-Domain"

    def test_no_domain_field_ok(self):
        p = make_page(fm_yaml="type: idea")
        assert types_of(check_dangling_domain_ref([p])) == []


# ────────────────────────────────────────────────────────────────────────
# check_missing_summary
# ────────────────────────────────────────────────────────────────────────


class TestCheckMissingSummary:
    def test_summary_present_ok(self):
        p = make_page(name="X", fm_yaml="type: idea\nsummary: 'something'")
        assert types_of(check_missing_summary([p])) == []

    def test_no_summary_flagged(self):
        p = make_page(name="X", fm_yaml="type: idea")
        issues = issues_of(check_missing_summary([p]))
        assert len(issues) == 1
        assert issues[0].type == "missing-summary"
        assert issues[0].payload["page_type"] == "idea"

    def test_empty_summary_flagged(self):
        p = make_page(name="X", fm_yaml='type: idea\nsummary: ""')
        issues = issues_of(check_missing_summary([p]))
        assert len(issues) == 1
        assert issues[0].type == "missing-summary"

    def test_whitespace_summary_flagged(self):
        p = make_page(name="X", fm_yaml="type: idea\nsummary: '   '")
        issues = issues_of(check_missing_summary([p]))
        assert len(issues) == 1

    def test_meta_pages_skipped(self):
        p = make_page(folder="meta", name="cache", fm_yaml="type: meta")
        assert types_of(check_missing_summary([p])) == []

    def test_all_content_types_required(self):
        for folder, ptype in [("ideas", "idea"), ("entities", "entity"),
                              ("domains", "domain"), ("questions", "question")]:
            p = make_page(folder=folder, name="X", fm_yaml=f"type: {ptype}")
            issues = issues_of(check_missing_summary([p]))
            assert len(issues) == 1, f"{folder} should require summary"


# ────────────────────────────────────────────────────────────────────────
# check_binary_source_outside_formats (filesystem-dependent)
# ────────────────────────────────────────────────────────────────────────


class TestCheckBinarySourceOutsideFormats:
    def test_pdf_in_raw_root_flagged(self, tmp_path, monkeypatch):
        monkeypatch.setattr(L, "RAW_ROOT", tmp_path)
        monkeypatch.setattr(L, "RAW_FORMATS_DIR", tmp_path / "formats")
        (tmp_path / "paper.pdf").write_bytes(b"%PDF")
        issues = issues_of(check_binary_source_outside_formats([]))
        assert len(issues) == 1
        assert issues[0].type == "binary-source-outside-formats"

    def test_pdf_in_formats_ok(self, tmp_path, monkeypatch):
        monkeypatch.setattr(L, "RAW_ROOT", tmp_path)
        monkeypatch.setattr(L, "RAW_FORMATS_DIR", tmp_path / "formats")
        formats = tmp_path / "formats"
        formats.mkdir()
        (formats / "paper.pdf").write_bytes(b"%PDF")
        assert types_of(check_binary_source_outside_formats([])) == []

    def test_md_file_in_raw_root_ok(self, tmp_path, monkeypatch):
        monkeypatch.setattr(L, "RAW_ROOT", tmp_path)
        monkeypatch.setattr(L, "RAW_FORMATS_DIR", tmp_path / "formats")
        (tmp_path / "notes.md").write_text("# Notes")
        assert types_of(check_binary_source_outside_formats([])) == []

    def test_audio_in_raw_root_flagged(self, tmp_path, monkeypatch):
        monkeypatch.setattr(L, "RAW_ROOT", tmp_path)
        monkeypatch.setattr(L, "RAW_FORMATS_DIR", tmp_path / "formats")
        (tmp_path / "lecture.mp3").write_bytes(b"")
        issues = issues_of(check_binary_source_outside_formats([]))
        assert issues[0].type == "binary-source-outside-formats"

    def test_meta_dir_exempt(self, tmp_path, monkeypatch):
        monkeypatch.setattr(L, "RAW_ROOT", tmp_path)
        monkeypatch.setattr(L, "RAW_FORMATS_DIR", tmp_path / "formats")
        meta = tmp_path / "meta"
        meta.mkdir()
        (meta / "ingested.json").write_text("{}")
        assert types_of(check_binary_source_outside_formats([])) == []


# ────────────────────────────────────────────────────────────────────────
# Auto-fix mutators (pure: text-in, text-out)
# ────────────────────────────────────────────────────────────────────────


def _fm_doc(fm_yaml: str, body: str = "") -> str:
    """Build a full markdown document with frontmatter for fix tests."""
    return f"---\n{fm_yaml}\n---\n{body}"


class TestResolveTemplater:
    def test_date_substituted(self):
        from datetime import date
        out = _resolve_templater('created: <% tp.date.now("YYYY-MM-DD") %>')
        assert date.today().isoformat() in out
        assert "tp.date" not in out

    def test_title_substituted(self):
        out = _resolve_templater(
            'domain:\n  - "[[<% tp.file.title %>]]"', page_title="Foo Bar"
        )
        assert "Foo Bar" in out
        assert "tp.file" not in out

    def test_no_placeholder_unchanged(self):
        assert _resolve_templater("status: evaluation") == "status: evaluation"


class TestFixStatusNotInEnum:
    def test_replaces_value(self):
        doc = _fm_doc("type: idea\nstatus: stub")
        out = _fix_status_not_in_enum_in_text(doc, {"fix": "in-progress"})
        assert "status: in-progress" in out
        assert "status: stub" not in out

    def test_only_first_status_replaced(self):
        # status appears in body — only frontmatter status touched
        doc = _fm_doc("type: idea\nstatus: stub", body="The status: stub note.\n")
        out = _fix_status_not_in_enum_in_text(doc, {"fix": "ready"})
        assert "status: ready" in out
        assert "The status: stub note." in out

    def test_no_frontmatter_no_change(self):
        out = _fix_status_not_in_enum_in_text("just text", {"fix": "ready"})
        assert out == "just text"


class TestFixFolderTypeMismatch:
    def test_replaces_type(self):
        doc = _fm_doc("type: idea\nstatus: ready")
        out = _fix_folder_type_mismatch_in_text(doc, {"expected_type": "entity"})
        assert "type: entity" in out
        assert "type: idea" not in out


class TestFixInlineTags:
    def test_simple_inline_to_block(self):
        doc = _fm_doc("type: idea\ntags: [ML, RL]")
        out = _fix_inline_tags_in_text(doc, {})
        assert "tags:\n  - ML\n  - RL" in out
        assert "tags: [" not in out

    def test_quoted_items_unquoted(self):
        doc = _fm_doc('type: idea\ntags: ["ML", "RL"]')
        out = _fix_inline_tags_in_text(doc, {})
        assert "tags:\n  - ML\n  - RL" in out

    def test_empty_list_stays_inline(self):
        doc = _fm_doc("type: idea\ntags: []")
        out = _fix_inline_tags_in_text(doc, {})
        assert "tags: []" in out

    def test_no_tags_no_change(self):
        doc = _fm_doc("type: idea\nstatus: ready")
        out = _fix_inline_tags_in_text(doc, {})
        assert out == doc


class TestFixNonCanonicalWikilink:
    def test_replaces_link(self):
        doc = "see [[wiki/ideas/RLHF]] here"
        out = _fix_non_canonical_wikilink_in_text(
            doc, {"link": "[[wiki/ideas/RLHF]]", "fix": "[[RLHF]]"}
        )
        assert out == "see [[RLHF]] here"

    def test_replaces_all_occurrences(self):
        doc = "[[wiki/ideas/X]] then [[wiki/ideas/X]]"
        out = _fix_non_canonical_wikilink_in_text(
            doc, {"link": "[[wiki/ideas/X]]", "fix": "[[X]]"}
        )
        assert out == "[[X]] then [[X]]"


class TestFixRawLinkWithExtension:
    def test_strips_md(self):
        doc = _fm_doc('type: idea\nsources:\n  - "[[raw/articles/X.md]]"')
        out = _fix_raw_link_with_extension_in_text(
            doc, {"link": "[[raw/articles/X.md]]"}
        )
        assert '"[[raw/articles/X]]"' in out
        assert ".md]]" not in out


class TestFixRawRefInBody:
    def test_removes_ref_with_leading_space(self):
        doc = _fm_doc("type: idea", body="From [[raw/X]] we know.")
        out = _fix_raw_ref_in_body_in_text(doc, {"link": "[[raw/X]]"})
        assert "[[raw/X]]" not in out
        assert "From we know." in out

    def test_frontmatter_untouched(self):
        # raw link in sources frontmatter should NOT be removed
        doc = _fm_doc(
            'type: idea\nsources:\n  - "[[raw/X]]"',
            body="text [[raw/X]] more",
        )
        out = _fix_raw_ref_in_body_in_text(doc, {"link": "[[raw/X]]"})
        assert '"[[raw/X]]"' in out  # frontmatter intact
        # body version removed
        assert "text  more" in out or "text more" in out


class TestFixInvalidFieldsExtra:
    def test_removes_simple_field(self):
        doc = _fm_doc("type: idea\nstatus: ready\nfoobar: x\ntags: []")
        out = _fix_invalid_fields_extra_in_text(doc, {"field": "foobar"})
        assert "foobar" not in out
        assert "type: idea" in out
        assert "tags: []" in out

    def test_removes_multiline_block(self):
        doc = _fm_doc(
            'type: idea\ndomain:\n  - "[[X]]"\n  - "[[Y]]"\ntags: []'
        )
        out = _fix_invalid_fields_extra_in_text(doc, {"field": "domain"})
        assert "domain" not in out
        assert "[[X]]" not in out
        assert "[[Y]]" not in out
        assert "tags: []" in out

    def test_field_not_present_no_change(self):
        doc = _fm_doc("type: idea\nstatus: ready")
        out = _fix_invalid_fields_extra_in_text(doc, {"field": "missing"})
        assert out == doc


class TestFixInvalidFieldsMissing:
    def test_appends_field_with_default(self):
        schemas = {
            "idea": {"status": "status: evaluation", "tags": "tags: []"}
        }
        doc = _fm_doc("type: idea")
        out = _fix_invalid_fields_missing_in_text(
            doc, {"field": "status"}, schemas=schemas
        )
        assert "status: evaluation" in out

    def test_appends_multiline_default(self):
        schemas = {
            "idea": {"domain": 'domain:\n  - "[[X]]"\n  - "[[Y]]"'}
        }
        doc = _fm_doc("type: idea\nstatus: ready")
        out = _fix_invalid_fields_missing_in_text(
            doc, {"field": "domain"}, schemas=schemas
        )
        assert 'domain:\n  - "[[X]]"\n  - "[[Y]]"' in out

    def test_substitutes_templater_date(self):
        from datetime import date
        schemas = {
            "idea": {"created": 'created: <% tp.date.now("YYYY-MM-DD") %>'}
        }
        doc = _fm_doc("type: idea")
        out = _fix_invalid_fields_missing_in_text(
            doc, {"field": "created"}, schemas=schemas
        )
        assert f"created: {date.today().isoformat()}" in out

    def test_unknown_type_no_change(self):
        schemas = {"idea": {"status": "status: evaluation"}}
        doc = _fm_doc("type: unknown")
        out = _fix_invalid_fields_missing_in_text(
            doc, {"field": "status"}, schemas=schemas
        )
        assert out == doc


# ────────────────────────────────────────────────────────────────────────
# apply_auto_fixes integration
# ────────────────────────────────────────────────────────────────────────


class TestApplyAutoFixes:
    def test_fixable_issue_fixed_and_dropped(self, tmp_path, monkeypatch):
        f = tmp_path / "page.md"
        f.write_text(_fm_doc("type: idea\nstatus: stub"))
        issues = [Issue("status-not-in-enum", {
            "where": str(f), "value": "stub", "fix": "in-progress"
        })]
        remaining, applied = apply_auto_fixes(issues)
        assert remaining == []
        assert len(applied) == 1
        assert applied[0]["type"] == "status-not-in-enum"
        assert applied[0]["where"] == str(f)
        assert "status: in-progress" in f.read_text()

    def test_unfixable_issue_kept(self):
        issues = [Issue("dead-link", {"where": "x.md", "what": "[[Y]]"})]
        remaining, applied = apply_auto_fixes(issues)
        assert applied == []
        assert len(remaining) == 1
        assert remaining[0].type == "dead-link"

    def test_mixed_issues(self, tmp_path):
        f = tmp_path / "page.md"
        f.write_text(_fm_doc("type: idea\nstatus: bad"))
        issues = [
            Issue("status-not-in-enum", {
                "where": str(f), "value": "bad", "fix": "ready"
            }),
            Issue("dead-link", {"where": str(f), "what": "[[Z]]"}),
        ]
        remaining, applied = apply_auto_fixes(issues)
        assert len(applied) == 1
        assert applied[0]["type"] == "status-not-in-enum"
        assert len(remaining) == 1
        assert remaining[0].type == "dead-link"

    def test_failed_fix_kept_as_remaining(self):
        # Path that doesn't exist → fix returns False → issue kept
        issues = [Issue("status-not-in-enum", {
            "where": "/nonexistent/page.md", "value": "x", "fix": "ready"
        })]
        remaining, applied = apply_auto_fixes(issues)
        assert applied == []
        assert len(remaining) == 1


# ────────────────────────────────────────────────────────────────────────
# compute_page_hashes / compute_touched_pages / issue_involves_pages
# ────────────────────────────────────────────────────────────────────────


class TestComputePageHashes:
    def test_per_page_hash_includes_full_content(self):
        a = make_page(name="A", fm_yaml="type: idea", body="hello")
        b = make_page(name="B", fm_yaml="type: idea", body="world")
        h = compute_page_hashes([a, b])
        assert set(h.keys()) == {"wiki/ideas/A.md", "wiki/ideas/B.md"}
        assert h["wiki/ideas/A.md"] != h["wiki/ideas/B.md"]

    def test_frontmatter_change_changes_hash(self):
        a = make_page(name="A", fm_yaml="type: idea\nstatus: ready", body="x")
        a2 = make_page(name="A", fm_yaml="type: idea\nstatus: draft", body="x")
        assert (
            compute_page_hashes([a])["wiki/ideas/A.md"]
            != compute_page_hashes([a2])["wiki/ideas/A.md"]
        )


class TestComputeTouchedPages:
    def test_bootstrap_returns_all(self):
        current = {"wiki/ideas/A.md": "h1", "wiki/ideas/B.md": "h2"}
        touched = compute_touched_pages(current, {})
        assert touched == {"wiki/ideas/A.md", "wiki/ideas/B.md"}

    def test_changed_page_detected(self):
        current = {"wiki/ideas/A.md": "h1-new", "wiki/ideas/B.md": "h2"}
        stored = {"wiki/ideas/A.md": "h1-old", "wiki/ideas/B.md": "h2"}
        assert compute_touched_pages(current, stored) == {"wiki/ideas/A.md"}

    def test_new_page_detected(self):
        # Page in current but not stored = new = touched
        current = {"wiki/ideas/A.md": "h1", "wiki/ideas/B.md": "h2"}
        stored = {"wiki/ideas/A.md": "h1"}
        assert compute_touched_pages(current, stored) == {"wiki/ideas/B.md"}

    def test_no_changes_empty(self):
        current = {"wiki/ideas/A.md": "h1", "wiki/ideas/B.md": "h2"}
        assert compute_touched_pages(current, current) == set()

    def test_deleted_page_not_touched(self):
        # Page in stored but not current — gone, not "touched"
        current = {"wiki/ideas/A.md": "h1"}
        stored = {"wiki/ideas/A.md": "h1", "wiki/ideas/B.md": "h2"}
        assert compute_touched_pages(current, stored) == set()


class TestIssueInvolvesPages:
    def test_where_field_matches(self):
        i = Issue("dead-link", {"where": "wiki/ideas/A.md", "what": "[[X]]"})
        assert issue_involves_pages(i, {"wiki/ideas/A.md"})
        assert not issue_involves_pages(i, {"wiki/ideas/B.md"})

    def test_pairwise_either_side_matches(self):
        i = Issue("contradiction", {
            "page_a": "wiki/ideas/A.md",
            "page_b": "wiki/ideas/B.md",
            "claim": "...",
        })
        assert issue_involves_pages(i, {"wiki/ideas/A.md"})
        assert issue_involves_pages(i, {"wiki/ideas/B.md"})
        assert not issue_involves_pages(i, {"wiki/ideas/C.md"})

    def test_mentioned_in_list_matches(self):
        i = Issue("missing-concept", {
            "term": "GAE",
            "mentioned_in": [
                "wiki/ideas/PPO.md",
                "wiki/ideas/Advantage Function.md",
            ],
        })
        assert issue_involves_pages(i, {"wiki/ideas/PPO.md"})
        assert not issue_involves_pages(i, {"wiki/entities/X.md"})

    def test_empty_set_matches_nothing(self):
        i = Issue("dead-link", {"where": "wiki/ideas/A.md", "what": "[[X]]"})
        assert not issue_involves_pages(i, set())


# ────────────────────────────────────────────────────────────────────────
# compute_wiki_hash
# ────────────────────────────────────────────────────────────────────────


class TestComputeWikiHash:
    def test_deterministic(self):
        pages = [
            make_page(name="A", fm_yaml="type: idea", body="Content A"),
            make_page(name="B", fm_yaml="type: idea", body="Content B"),
        ]
        h1 = compute_wiki_hash(pages)
        h2 = compute_wiki_hash(pages)
        assert h1 == h2

    def test_different_content_different_hash(self):
        pages_a = [make_page(name="A", fm_yaml="type: idea", body="Content A")]
        pages_b = [make_page(name="A", fm_yaml="type: idea", body="Content B")]
        assert compute_wiki_hash(pages_a) != compute_wiki_hash(pages_b)

    def test_order_independent(self):
        a = make_page(name="A", fm_yaml="type: idea")
        b = make_page(name="B", fm_yaml="type: idea")
        assert compute_wiki_hash([a, b]) == compute_wiki_hash([b, a])

    def test_empty_returns_hash(self):
        h = compute_wiki_hash([])
        assert isinstance(h, str) and len(h) == 64


# ────────────────────────────────────────────────────────────────────────
# Layer 1.5 — embedding-based checks
# ────────────────────────────────────────────────────────────────────────


class TestWikilinkToRawKey:
    def test_simple(self):
        assert _wikilink_to_raw_key("[[raw/RLHF]]") == "RLHF.md"

    def test_subpath(self):
        assert _wikilink_to_raw_key("[[raw/articles/foo]]") == "articles/foo.md"

    def test_compound_extension_kept(self):
        assert _wikilink_to_raw_key("[[raw/paper.docx.md]]") == "paper.docx.md"

    def test_already_md(self):
        assert _wikilink_to_raw_key("[[raw/note.md]]") == "note.md"

    def test_non_raw_returns_none(self):
        assert _wikilink_to_raw_key("[[wiki/Page]]") is None

    def test_not_a_wikilink(self):
        assert _wikilink_to_raw_key("just text") is None


def _make_idx(pairs: list[tuple[str, list[float]]]) -> EmbedIndex:
    """Helper: build an in-memory EmbedIndex with given (name, vec) pairs."""
    idx = EmbedIndex(Path("/dev/null"))
    for name, vec in pairs:
        idx.upsert(name, f"content for {name}", vec)
    return idx


class TestCheckSimilarButUnlinked:
    def test_similar_unlinked_flagged(self):
        # A and B have nearly identical vectors; no link between them
        a = make_page(name="A", fm_yaml="type: idea")
        b = make_page(name="B", fm_yaml="type: idea")
        # Add a third dissimilar page so threshold makes sense
        c = make_page(name="C", fm_yaml="type: idea")
        idx = _make_idx([
            ("A", [1.0, 0.0, 0.0]),
            ("B", [0.99, 0.01, 0.0]),
            ("C", [0.0, 0.0, 1.0]),
        ])
        issues = list(check_similar_but_unlinked([a, b, c], idx, threshold_percentile=50))
        assert len(issues) == 1
        assert issues[0].type == "similar-but-unlinked"
        flagged = {issues[0].payload["page_a"], issues[0].payload["page_b"]}
        assert flagged == {a.relpath(), b.relpath()}

    def test_similar_but_linked_skipped(self):
        # A→B via body wikilink
        a = make_page(name="A", fm_yaml="type: idea", body="See [[B]].\n")
        b = make_page(name="B", fm_yaml="type: idea")
        c = make_page(name="C", fm_yaml="type: idea")
        idx = _make_idx([
            ("A", [1.0, 0.0]),
            ("B", [0.99, 0.01]),
            ("C", [0.0, 1.0]),
        ])
        issues = list(check_similar_but_unlinked([a, b, c], idx, threshold_percentile=50))
        assert len(issues) == 0

    def test_linked_via_related_skipped(self):
        a = make_page(name="A", fm_yaml='type: idea\nrelated:\n  - "[[B]]"')
        b = make_page(name="B", fm_yaml="type: idea")
        c = make_page(name="C", fm_yaml="type: idea")
        idx = _make_idx([
            ("A", [1.0, 0.0]),
            ("B", [0.99, 0.01]),
            ("C", [0.0, 1.0]),
        ])
        issues = list(check_similar_but_unlinked([a, b, c], idx, threshold_percentile=50))
        assert len(issues) == 0

    def test_dissimilar_pages_not_flagged(self):
        a = make_page(name="A", fm_yaml="type: idea")
        b = make_page(name="B", fm_yaml="type: idea")
        idx = _make_idx([
            ("A", [1.0, 0.0]),
            ("B", [0.0, 1.0]),  # orthogonal
        ])
        issues = list(check_similar_but_unlinked([a, b], idx, threshold_percentile=99))
        assert len(issues) == 0

    def test_empty_index_returns_no_issues(self):
        a = make_page(name="A", fm_yaml="type: idea")
        idx = EmbedIndex(Path("/dev/null"))
        assert list(check_similar_but_unlinked([a], idx)) == []

    def test_stale_index_entry_skipped(self):
        # Embedding for a page that no longer exists in the wiki — skip silently
        a = make_page(name="A", fm_yaml="type: idea")
        idx = _make_idx([
            ("A", [1.0, 0.0]),
            ("Deleted-Page", [0.99, 0.01]),
        ])
        issues = list(check_similar_but_unlinked([a], idx, threshold_percentile=50))
        # No pair to flag — Deleted-Page isn't in pages list
        assert len(issues) == 0

    def test_pair_reported_once(self):
        # Symmetric pair (A,B) should produce at most one issue
        a = make_page(name="A", fm_yaml="type: idea")
        b = make_page(name="B", fm_yaml="type: idea")
        idx = _make_idx([
            ("A", [1.0, 0.0]),
            ("B", [0.99, 0.01]),
        ])
        issues = list(check_similar_but_unlinked([a, b], idx, threshold_percentile=50))
        assert len(issues) <= 1

    def test_meta_pages_excluded(self):
        # cache.md and summary.md naturally have similar embeddings (both
        # describe wiki state) but they're infrastructure — must not be flagged.
        cache = make_page(folder="", name="cache", fm_yaml="type: meta")
        summary = make_page(folder="", name="summary", fm_yaml="type: meta")
        # Add a content page so threshold is meaningful
        idea = make_page(name="Idea", fm_yaml="type: idea")
        idx = _make_idx([
            ("cache", [1.0, 0.0]),
            ("summary", [0.99, 0.01]),
            ("Idea", [0.0, 1.0]),
        ])
        issues = list(check_similar_but_unlinked(
            [cache, summary, idea], idx, threshold_percentile=50,
        ))
        assert len(issues) == 0

    def test_meta_folder_excluded(self):
        # Pages in wiki/meta/ (lint reports, base files etc.) also excluded
        m1 = make_page(folder="meta", name="dashboard", fm_yaml="type: meta")
        m2 = make_page(folder="meta", name="report", fm_yaml="type: meta")
        idea = make_page(name="X", fm_yaml="type: idea")
        idx = _make_idx([
            ("dashboard", [1.0, 0.0]),
            ("report", [0.99, 0.01]),
            ("X", [0.0, 1.0]),
        ])
        issues = list(check_similar_but_unlinked(
            [m1, m2, idea], idx, threshold_percentile=50,
        ))
        assert len(issues) == 0


class TestCheckSynthesisDrift:
    def test_low_drift_not_flagged(self):
        # Wiki page vec ~= source vec → near-zero drift, not flagged
        page = make_page(
            name="P",
            fm_yaml='type: idea\nsources:\n  - "[[raw/source]]"',
        )
        wiki_idx = _make_idx([("P", [1.0, 0.0, 0.0])])
        raw_idx = _make_idx([("source.md", [1.0, 0.0, 0.0])])
        issues = list(check_synthesis_drift([page], wiki_idx, raw_idx))
        # All drifts are zero — nothing exceeds mean+std
        assert len(issues) == 0

    def test_high_drift_outlier_flagged(self):
        # One page drifts significantly from its source while others don't
        good_pages = [
            make_page(name=f"G{i}", fm_yaml=f'type: idea\nsources:\n  - "[[raw/s{i}]]"')
            for i in range(5)
        ]
        drifted = make_page(
            name="Drifted",
            fm_yaml='type: idea\nsources:\n  - "[[raw/sD]]"',
        )
        wiki_pairs = [(f"G{i}", [1.0, 0.0, 0.0]) for i in range(5)]
        raw_pairs = [(f"s{i}.md", [1.0, 0.0, 0.0]) for i in range(5)]
        # Drifted page is orthogonal to its source
        wiki_pairs.append(("Drifted", [0.0, 1.0, 0.0]))
        raw_pairs.append(("sD.md", [1.0, 0.0, 0.0]))
        wiki_idx = _make_idx(wiki_pairs)
        raw_idx = _make_idx(raw_pairs)
        issues = list(check_synthesis_drift(good_pages + [drifted], wiki_idx, raw_idx))
        assert any(i.payload["where"].endswith("/Drifted.md") for i in issues)

    def test_no_sources_skipped(self):
        page = make_page(name="P", fm_yaml="type: idea")
        wiki_idx = _make_idx([("P", [1.0, 0.0])])
        raw_idx = _make_idx([("any.md", [0.5, 0.5])])
        assert list(check_synthesis_drift([page], wiki_idx, raw_idx)) == []

    def test_sources_not_in_raw_index_skipped(self):
        page = make_page(
            name="P",
            fm_yaml='type: idea\nsources:\n  - "[[raw/missing]]"',
        )
        wiki_idx = _make_idx([("P", [1.0, 0.0])])
        raw_idx = _make_idx([("other.md", [0.5, 0.5])])
        # No raw vector for the source → skip
        assert list(check_synthesis_drift([page], wiki_idx, raw_idx)) == []

    def test_empty_indexes_return_no_issues(self):
        page = make_page(name="P", fm_yaml="type: idea")
        empty = EmbedIndex(Path("/dev/null"))
        assert list(check_synthesis_drift([page], empty, empty)) == []

    def test_compound_source_extension(self):
        # [[raw/paper.docx.md]] should look up "paper.docx.md" in raw_idx
        page = make_page(
            name="P",
            fm_yaml='type: idea\nsources:\n  - "[[raw/paper.docx.md]]"',
        )
        wiki_idx = _make_idx([("P", [1.0, 0.0])])
        raw_idx = _make_idx([("paper.docx.md", [1.0, 0.0])])
        # Should find the source successfully — no issue (drift is 0)
        issues = list(check_synthesis_drift([page], wiki_idx, raw_idx))
        assert len(issues) == 0

    def test_multiple_sources_aggregated(self):
        # Sources are averaged into a centroid before comparing
        page = make_page(
            name="P",
            fm_yaml='type: idea\nsources:\n  - "[[raw/s1]]"\n  - "[[raw/s2]]"',
        )
        wiki_idx = _make_idx([("P", [0.5, 0.5])])  # centroid of sources
        raw_idx = _make_idx([
            ("s1.md", [1.0, 0.0]),
            ("s2.md", [0.0, 1.0]),
        ])
        # Wiki vec equals centroid of sources → very low drift
        issues = list(check_synthesis_drift([page], wiki_idx, raw_idx))
        assert len(issues) == 0

    def test_mind_type_skipped(self):
        # mind pages are author reflections, not source syntheses — drift is
        # not a meaningful signal. The check must skip them entirely, even when
        # other (non-mind) pages have the variance needed for the threshold to fire.
        good_pages = [
            make_page(name=f"G{i}", fm_yaml=f'type: idea\nsources:\n  - "[[raw/s{i}]]"')
            for i in range(5)
        ]
        # Mind orthogonal to its source — would be flagged if check applied
        mind = make_page(
            name="MyMind",
            folder="minds",
            fm_yaml='type: mind\nsources:\n  - "[[raw/brainstorm/test]]"',
        )
        wiki_pairs = [(f"G{i}", [1.0, 0.0, 0.0]) for i in range(5)]
        raw_pairs = [(f"s{i}.md", [1.0, 0.0, 0.0]) for i in range(5)]
        wiki_pairs.append(("MyMind", [0.0, 1.0, 0.0]))
        raw_pairs.append(("brainstorm/test.md", [1.0, 0.0, 0.0]))
        wiki_idx = _make_idx(wiki_pairs)
        raw_idx = _make_idx(raw_pairs)
        issues = list(check_synthesis_drift(good_pages + [mind], wiki_idx, raw_idx))
        assert not any(i.payload["where"].endswith("/MyMind.md") for i in issues)


# ────────────────────────────────────────────────────────────────────────
# compute_contradiction_candidates (Layer 2 pre-filter)
# ────────────────────────────────────────────────────────────────────────


class TestComputeContradictionCandidates:
    def test_high_similarity_pairs_returned(self):
        a = make_page(name="A", fm_yaml="type: idea")
        b = make_page(name="B", fm_yaml="type: idea")
        c = make_page(name="C", fm_yaml="type: idea")
        idx = _make_idx([
            ("A", [1.0, 0.0]),
            ("B", [0.95, 0.05]),
            ("C", [0.0, 1.0]),
        ])
        candidates = compute_contradiction_candidates(
            [a, b, c], idx, threshold_percentile=50,
        )
        # A and B are highly similar; C is orthogonal
        assert len(candidates) == 1
        flagged = {candidates[0]["page_a"], candidates[0]["page_b"]}
        assert flagged == {a.relpath(), b.relpath()}

    def test_below_floor_excluded(self):
        # All similarities below the 0.5 floor → no candidates
        a = make_page(name="A", fm_yaml="type: idea")
        b = make_page(name="B", fm_yaml="type: idea")
        idx = _make_idx([
            ("A", [1.0, 0.0]),
            ("B", [0.0, 1.0]),  # cosine = 0
        ])
        assert compute_contradiction_candidates([a, b], idx, threshold_percentile=50) == []

    def test_meta_pages_excluded(self):
        cache = make_page(folder="", name="cache", fm_yaml="type: meta")
        summary = make_page(folder="", name="summary", fm_yaml="type: meta")
        idea = make_page(name="X", fm_yaml="type: idea")
        idx = _make_idx([
            ("cache", [1.0, 0.0]),
            ("summary", [0.99, 0.01]),  # high sim with cache
            ("X", [0.0, 1.0]),
        ])
        candidates = compute_contradiction_candidates(
            [cache, summary, idea], idx, threshold_percentile=50,
        )
        # cache+summary pair must not appear despite high similarity
        assert candidates == []

    def test_sorted_by_similarity_descending(self):
        a = make_page(name="A", fm_yaml="type: idea")
        b = make_page(name="B", fm_yaml="type: idea")
        c = make_page(name="C", fm_yaml="type: idea")
        d = make_page(name="D", fm_yaml="type: idea")
        idx = _make_idx([
            ("A", [1.0, 0.0]),
            ("B", [0.95, 0.05]),    # cos(A,B) ≈ 0.9999
            ("C", [0.7, 0.7]),      # moderate sim with both A and B
            ("D", [0.6, 0.8]),      # also moderate
        ])
        candidates = compute_contradiction_candidates(
            [a, b, c, d], idx, threshold_percentile=10, min_similarity=0.5,
        )
        # Verify sorted descending
        sims = [c["similarity"] for c in candidates]
        assert sims == sorted(sims, reverse=True)

    def test_empty_index(self):
        idx = EmbedIndex(Path("/dev/null"))
        a = make_page(name="A", fm_yaml="type: idea")
        assert compute_contradiction_candidates([a], idx) == []

    def test_pair_reported_once(self):
        # Symmetric pair — only one entry, never (A,B) and (B,A)
        a = make_page(name="A", fm_yaml="type: idea")
        b = make_page(name="B", fm_yaml="type: idea")
        c = make_page(name="C", fm_yaml="type: idea")  # orthogonal — avoids degenerate distribution
        idx = _make_idx([
            ("A", [1.0, 0.0]),
            ("B", [0.99, 0.01]),
            ("C", [0.0, 1.0]),
        ])
        candidates = compute_contradiction_candidates(
            [a, b, c], idx, threshold_percentile=50, min_similarity=0.5,
        )
        assert len(candidates) == 1

    def test_stale_index_entry_skipped(self):
        # Embedding for non-existent page is silently skipped
        a = make_page(name="A", fm_yaml="type: idea")
        idx = _make_idx([
            ("A", [1.0, 0.0]),
            ("Deleted", [0.99, 0.01]),
        ])
        candidates = compute_contradiction_candidates(
            [a], idx, threshold_percentile=10, min_similarity=0.5,
        )
        # Need 2 valid pages to form a pair; only A is valid → empty
        assert candidates == []

    def test_includes_already_linked_pairs(self):
        # Unlike check_similar_but_unlinked, this DOES include linked pairs —
        # contradictions can exist between pages that are already linked.
        a = make_page(name="A", fm_yaml="type: idea", body="See [[B]].\n")
        b = make_page(name="B", fm_yaml="type: idea")
        c = make_page(name="C", fm_yaml="type: idea")
        idx = _make_idx([
            ("A", [1.0, 0.0]),
            ("B", [0.95, 0.05]),
            ("C", [0.0, 1.0]),
        ])
        candidates = compute_contradiction_candidates(
            [a, b, c], idx, threshold_percentile=50, min_similarity=0.5,
        )
        # Linked or not — both pairs go to Layer 2 for inspection
        assert len(candidates) == 1
