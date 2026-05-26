"""Unit tests for bin/rename_wiki_page.py.

Coverage:
- wiki rename: все формы wikilink (canonical, anchor, alias, embed, ну и
  все комбинации); boundary/substring safety; frontmatter обновляется
- raw move: формы wikilink на raw-источники; path-uniqueness (не трогает
  одноимённые файлы в других папках); расширение сохраняется для бинарей
- ошибки: несуществующий source, существующий target, cross-root
- mv реально происходит после обновления ссылок
- legacy non-canonical формы не трогаются (это работа lint'а)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make `bin/` importable regardless of cwd
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rename_wiki_page as RWP
from rename_wiki_page import (
    build_pattern,
    detect_mode,
    link_target_for,
    rename,
    replace_in_text,
)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Set up an empty vault layout under tmp_path with monkeypatched roots."""
    wiki = tmp_path / "wiki"
    raw = tmp_path / "raw"
    (wiki / "ideas").mkdir(parents=True)
    (wiki / "entities").mkdir(parents=True)
    (wiki / "domains").mkdir(parents=True)
    (wiki / "questions").mkdir(parents=True)
    (raw / "articles").mkdir(parents=True)
    (raw / "formats").mkdir(parents=True)

    monkeypatch.setattr(RWP, "ROOT", tmp_path)
    monkeypatch.setattr(RWP, "WIKI_ROOT", wiki)
    monkeypatch.setattr(RWP, "RAW_ROOT", raw)
    return tmp_path


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ────────────────────────────────────────────────────────────────────────
# Pure helpers
# ────────────────────────────────────────────────────────────────────────


class TestDetectMode:
    def test_wiki_path(self, vault):
        assert detect_mode(vault / "wiki" / "ideas" / "X.md") == "wiki"

    def test_raw_path(self, vault):
        assert detect_mode(vault / "raw" / "articles" / "X.md") == "raw"

    def test_outside_vault_raises(self, vault):
        with pytest.raises(ValueError):
            detect_mode(vault / "_attachments" / "img.png")


class TestLinkTargetFor:
    def test_wiki_md_returns_basename(self, vault):
        assert link_target_for(vault / "wiki" / "ideas" / "RLHF.md") == "RLHF"

    def test_wiki_with_spaces(self, vault):
        assert link_target_for(vault / "wiki" / "entities" / "Andrej Karpathy.md") == "Andrej Karpathy"

    def test_raw_md_strips_extension(self, vault):
        assert link_target_for(vault / "raw" / "articles" / "foo.md") == "raw/articles/foo"

    def test_raw_pdf_keeps_extension(self, vault):
        assert link_target_for(vault / "raw" / "formats" / "X.pdf") == "raw/formats/X.pdf"

    def test_raw_root_md(self, vault):
        assert link_target_for(vault / "raw" / "PPO.md") == "raw/PPO"


class TestBuildPattern:
    def test_matches_canonical(self):
        p = build_pattern("RLHF")
        assert p.search("See [[RLHF]] here")
        assert not p.search("See [[Other]] here")

    def test_matches_with_anchor(self):
        p = build_pattern("RLHF")
        assert p.search("See [[RLHF#Section]] here")

    def test_matches_with_alias(self):
        p = build_pattern("RLHF")
        assert p.search("See [[RLHF|альт]] here")

    def test_matches_anchor_and_alias(self):
        p = build_pattern("RLHF")
        assert p.search("See [[RLHF#Section|альт]] here")

    def test_matches_embed(self):
        p = build_pattern("RLHF")
        assert p.search("![[RLHF]] embedded")

    def test_does_not_match_extended_basename(self):
        # [[Foo]] when target=Bar — no match
        p = build_pattern("Foo")
        assert not p.search("[[Foo Extended]]")  # space — actually [[Foo]] would match if just Foo
        assert not p.search("[[FooBar]]")  # FooBar starts with Foo but not equal
        assert not p.search("[[BarFoo]]")  # ends with Foo

    def test_does_not_match_path_prefixed(self):
        # legacy [[wiki/ideas/Foo]] — not handled here, lint's job
        p = build_pattern("Foo")
        assert not p.search("[[wiki/ideas/Foo]]")

    def test_target_with_special_regex_chars(self):
        # link target with dots/parens — should be re.escape'd
        p = build_pattern("Smith (2020)")
        assert p.search("[[Smith (2020)]]")


# ────────────────────────────────────────────────────────────────────────
# replace_in_text
# ────────────────────────────────────────────────────────────────────────


class TestReplaceInText:
    def test_simple_replace(self):
        p = build_pattern("Old")
        assert replace_in_text("See [[Old]] here", p, "New") == "See [[New]] here"

    def test_preserves_embed(self):
        p = build_pattern("Old")
        assert replace_in_text("![[Old]]", p, "New") == "![[New]]"

    def test_preserves_anchor(self):
        p = build_pattern("Old")
        assert replace_in_text("[[Old#Sec]]", p, "New") == "[[New#Sec]]"

    def test_preserves_alias(self):
        p = build_pattern("Old")
        assert replace_in_text("[[Old|кастом]]", p, "New") == "[[New|кастом]]"

    def test_preserves_anchor_and_alias(self):
        p = build_pattern("Old")
        assert replace_in_text("[[Old#Sec|кастом]]", p, "New") == "[[New#Sec|кастом]]"

    def test_multiple_occurrences(self):
        p = build_pattern("Old")
        result = replace_in_text("[[Old]] and [[Old#A]] and ![[Old|x]]", p, "New")
        assert result == "[[New]] and [[New#A]] and ![[New|x]]"

    def test_does_not_touch_other_links(self):
        p = build_pattern("Old")
        text = "[[Old]] but not [[Other]]"
        assert replace_in_text(text, p, "New") == "[[New]] but not [[Other]]"


# ────────────────────────────────────────────────────────────────────────
# rename() — wiki mode
# ────────────────────────────────────────────────────────────────────────


class TestWikiRename:
    def test_simple_rename(self, vault):
        old = write(vault / "wiki" / "ideas" / "Old.md", "# Old\nself ref [[Old]]\n")
        ref = write(vault / "wiki" / "ideas" / "Ref.md", "links to [[Old]] there\n")
        new = vault / "wiki" / "ideas" / "New.md"

        code, changed = rename(old, new)
        assert code == 0
        assert not old.exists()
        assert new.exists()
        # Ref-страница обновлена
        assert ref.read_text() == "links to [[New]] there\n"
        # И сам переименованный файл (его self-reference тоже обновляется)
        assert new.read_text() == "# Old\nself ref [[New]]\n"

    def test_all_link_forms_updated(self, vault):
        old = write(vault / "wiki" / "ideas" / "Old.md", "# Old\n")
        ref = write(
            vault / "wiki" / "entities" / "Ref.md",
            "[[Old]] [[Old#Sec]] [[Old|alias]] [[Old#A|b]] ![[Old]]\n",
        )
        new = vault / "wiki" / "ideas" / "New.md"

        code, _ = rename(old, new)
        assert code == 0
        assert ref.read_text() == "[[New]] [[New#Sec]] [[New|alias]] [[New#A|b]] ![[New]]\n"

    def test_substring_safety(self, vault):
        old = write(vault / "wiki" / "ideas" / "Old.md", "")
        ref = write(
            vault / "wiki" / "ideas" / "Ref.md",
            "[[Old]] is renamed; [[OldExtended]] is not; [[OtherOld]] no.\n",
        )
        new = vault / "wiki" / "ideas" / "New.md"

        rename(old, new)
        assert ref.read_text() == "[[New]] is renamed; [[OldExtended]] is not; [[OtherOld]] no.\n"

    def test_path_prefixed_legacy_not_touched(self, vault):
        # Path-prefixed формы — это work for lint's `non-canonical-wikilink`.
        # rename_wiki_page их не трогает, оставляет как есть.
        old = write(vault / "wiki" / "ideas" / "Old.md", "")
        ref = write(
            vault / "wiki" / "ideas" / "Ref.md",
            "canonical [[Old]], legacy [[wiki/ideas/Old]]\n",
        )
        new = vault / "wiki" / "ideas" / "New.md"

        rename(old, new)
        assert ref.read_text() == "canonical [[New]], legacy [[wiki/ideas/Old]]\n"

    def test_frontmatter_links_updated(self, vault):
        old = write(vault / "wiki" / "ideas" / "Old.md", "")
        ref = write(
            vault / "wiki" / "entities" / "Ref.md",
            '---\ntype: entity\nrelated:\n  - "[[Old]]"\n  - "[[Other]]"\ndomain:\n  - "[[Old]]"\nsources:\n  - "[[raw/foo]]"\n---\nbody\n',
        )
        new = vault / "wiki" / "ideas" / "New.md"

        rename(old, new)
        text = ref.read_text()
        assert '"[[New]]"' in text
        assert '"[[Other]]"' in text  # untouched
        assert '"[[raw/foo]]"' in text  # untouched (raw, not wiki target)

    def test_move_across_wiki_folders(self, vault):
        # Можно переместить страницу из ideas/ в entities/, basename меняется
        old = write(vault / "wiki" / "ideas" / "OldIdea.md", "")
        ref = write(vault / "wiki" / "entities" / "Ref.md", "[[OldIdea]]\n")
        new = vault / "wiki" / "entities" / "NewEntity.md"

        code, _ = rename(old, new)
        assert code == 0
        assert ref.read_text() == "[[NewEntity]]\n"
        assert new.exists()
        assert not old.exists()

    def test_rename_with_special_chars_in_basename(self, vault):
        old = write(vault / "wiki" / "entities" / "Smith (2020) — Paper.md", "")
        ref = write(vault / "wiki" / "ideas" / "Ref.md", "[[Smith (2020) — Paper]]\n")
        new = vault / "wiki" / "entities" / "Smith 2020.md"

        rename(old, new)
        assert ref.read_text() == "[[Smith 2020]]\n"


# ────────────────────────────────────────────────────────────────────────
# rename() — raw mode
# ────────────────────────────────────────────────────────────────────────


class TestRawMove:
    def test_md_move_strips_extension_in_link(self, vault):
        # raw/articles/foo.md → raw/articles/bar.md
        # link [[raw/articles/foo]] → [[raw/articles/bar]]
        old = write(vault / "raw" / "articles" / "foo.md", "transcript\n")
        ref = write(
            vault / "wiki" / "ideas" / "Ref.md",
            'frontmatter source [[raw/articles/foo]] in body too\n',
        )
        new = vault / "raw" / "articles" / "bar.md"

        code, changed = rename(old, new)
        assert code == 0
        assert new.exists()
        assert not old.exists()
        assert ref.read_text() == "frontmatter source [[raw/articles/bar]] in body too\n"
        assert ref in changed

    def test_pdf_move_keeps_extension(self, vault):
        # raw/X.pdf → raw/formats/X.pdf
        # link [[raw/X.pdf]] → [[raw/formats/X.pdf]]
        old = write(vault / "raw" / "X.pdf", "%PDF\n")
        ref = write(vault / "wiki" / "ideas" / "Ref.md", "embed: ![[raw/X.pdf]]\n")
        new = vault / "raw" / "formats" / "X.pdf"

        code, _ = rename(old, new)
        assert code == 0
        assert ref.read_text() == "embed: ![[raw/formats/X.pdf]]\n"

    def test_raw_path_uniqueness(self, vault):
        # Если есть raw/articles/foo.md и raw/transcripts/foo.md, перемещение
        # одного из них не должно затрагивать ссылку на другой.
        (vault / "raw" / "transcripts").mkdir()
        write(vault / "raw" / "transcripts" / "foo.md", "")
        old = write(vault / "raw" / "articles" / "foo.md", "")
        ref = write(
            vault / "wiki" / "ideas" / "Ref.md",
            "article [[raw/articles/foo]], transcript [[raw/transcripts/foo]]\n",
        )
        new = vault / "raw" / "articles" / "bar.md"

        rename(old, new)
        assert ref.read_text() == "article [[raw/articles/bar]], transcript [[raw/transcripts/foo]]\n"

    def test_raw_link_forms(self, vault):
        old = write(vault / "raw" / "articles" / "foo.md", "")
        ref = write(
            vault / "wiki" / "ideas" / "Ref.md",
            "[[raw/articles/foo]] [[raw/articles/foo#A]] [[raw/articles/foo|cite]] ![[raw/articles/foo]]\n",
        )
        new = vault / "raw" / "articles" / "bar.md"

        rename(old, new)
        assert ref.read_text() == (
            "[[raw/articles/bar]] [[raw/articles/bar#A]] [[raw/articles/bar|cite]] ![[raw/articles/bar]]\n"
        )


# ────────────────────────────────────────────────────────────────────────
# Validation errors
# ────────────────────────────────────────────────────────────────────────


class TestValidation:
    def test_old_path_missing(self, vault, capsys):
        old = vault / "wiki" / "ideas" / "Nope.md"
        new = vault / "wiki" / "ideas" / "New.md"
        code, _ = rename(old, new)
        assert code == 1
        assert "does not exist" in capsys.readouterr().err

    def test_new_path_already_exists(self, vault, capsys):
        old = write(vault / "wiki" / "ideas" / "Old.md", "")
        new = write(vault / "wiki" / "ideas" / "Existing.md", "")
        code, _ = rename(old, new)
        assert code == 1
        assert "already exists" in capsys.readouterr().err
        # Ничего не двигается
        assert old.exists()
        assert new.exists()

    def test_cross_root_wiki_to_raw(self, vault, capsys):
        old = write(vault / "wiki" / "ideas" / "Old.md", "")
        new = vault / "raw" / "Old.md"
        code, _ = rename(old, new)
        assert code == 1
        assert "cross-root" in capsys.readouterr().err
        assert old.exists()

    def test_cross_root_raw_to_wiki(self, vault, capsys):
        old = write(vault / "raw" / "X.md", "")
        new = vault / "wiki" / "ideas" / "X.md"
        code, _ = rename(old, new)
        assert code == 1
        assert "cross-root" in capsys.readouterr().err

    def test_outside_vault_path(self, vault, capsys):
        old = write(vault / "_attachments" / "img.md", "")
        new = vault / "wiki" / "ideas" / "img.md"
        code, _ = rename(old, new)
        assert code == 1


# ────────────────────────────────────────────────────────────────────────
# File system effects
# ────────────────────────────────────────────────────────────────────────


class TestFileSystemEffects:
    def test_move_creates_parent_dir_if_needed(self, vault):
        # move в новую подпапку под wiki/ — родителя ещё нет
        new_dir = vault / "wiki" / "newcat"
        old = write(vault / "wiki" / "ideas" / "Old.md", "")
        new = new_dir / "Old.md"
        # newcat не существует
        assert not new_dir.exists()
        code, _ = rename(old, new)
        assert code == 0
        assert new.exists()

    def test_only_changed_files_returned(self, vault):
        old = write(vault / "wiki" / "ideas" / "Old.md", "")
        write(vault / "wiki" / "ideas" / "Untouched.md", "no link here\n")
        ref = write(vault / "wiki" / "ideas" / "Ref.md", "[[Old]]\n")
        new = vault / "wiki" / "ideas" / "New.md"

        _, changed = rename(old, new)
        # Только Ref должен быть в списке (Untouched не содержит [[Old]])
        # Сам Old.md не имеет [[Old]], значит тоже не в списке
        names = {p.name for p in changed}
        assert "Ref.md" in names
        assert "Untouched.md" not in names

    def test_no_op_when_no_references(self, vault):
        old = write(vault / "wiki" / "ideas" / "Old.md", "")
        new = vault / "wiki" / "ideas" / "New.md"
        code, changed = rename(old, new)
        assert code == 0
        assert changed == []
        assert new.exists()
