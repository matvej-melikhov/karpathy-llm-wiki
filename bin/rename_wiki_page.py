#!/usr/bin/env python3
"""Безопасное переименование wiki-страницы или перемещение raw-источника.
Перед `mv` обновляет все wikilinks по vault'у, чтобы ссылки не сломались.

Usage:
    python3 bin/rename_wiki_page.py <old_path> <new_path>

Mode auto-detect по расположению:
- `wiki/<X>/...` → wiki rename, target = basename без `.md`
- `raw/...` → raw move, target = relpath от корня репо без `.md`-расширения
  (для бинарей расширение сохраняется: `raw/X.pdf` → target `raw/X.pdf`)

Оба пути должны быть под одним корнем (wiki/ или raw/). Cross-root запрещён.
new_path не должен существовать.

Скрипт обрабатывает все формы wikilink: `[[X]]`, `[[X#anchor]]`, `[[X|alias]]`,
`[[X#anchor|alias]]`, `![[X]]` (embed). Не трогает legacy non-canonical
формы — это работа `bin/lint.py` (`non-canonical-wikilink`,
`raw-link-with-extension`).

Exit codes:
  0 — успех
  1 — ошибка валидации (несуществующий old, существующий new, cross-root)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WIKI_ROOT = ROOT / "wiki"
RAW_ROOT = ROOT / "raw"


def detect_mode(path: Path) -> str:
    """'wiki' или 'raw' по расположению пути относительно vault'а.

    Бросает ValueError если путь не под wiki/ или raw/.
    """
    try:
        path.relative_to(WIKI_ROOT)
        return "wiki"
    except ValueError:
        pass
    try:
        path.relative_to(RAW_ROOT)
        return "raw"
    except ValueError:
        pass
    raise ValueError(f"path not under wiki/ or raw/: {path}")


def link_target_for(path: Path) -> str:
    """Канонический wikilink-target для заданного файла.

    wiki/<X>/foo.md → 'foo'
    raw/articles/foo.md → 'raw/articles/foo'  (.md strip)
    raw/X.pdf → 'raw/X.pdf'  (расширение для бинарей сохраняется)
    """
    mode = detect_mode(path)
    if mode == "wiki":
        return path.stem
    rel = path.relative_to(ROOT)
    if rel.suffix == ".md":
        return str(rel.with_suffix(""))
    return str(rel)


def build_pattern(target: str) -> re.Pattern[str]:
    """Regex для `[[target]]` с опциональным `!`-embed, `#anchor`, `|alias`.

    Граница `\\]\\]` после опциональных частей предотвращает ложное
    срабатывание на `[[Old]]` когда переименовываем `[[OldExtended]]`.
    """
    return re.compile(
        r"(!?)\[\[" + re.escape(target) + r"(#[^\]|]*)?(\|[^\]]*)?\]\]"
    )


def replace_in_text(text: str, pattern: re.Pattern[str], new_target: str) -> str:
    """Заменить все вхождения pattern в тексте, сохраняя embed/anchor/alias."""
    def sub(m: re.Match[str]) -> str:
        embed = m.group(1) or ""
        anchor = m.group(2) or ""
        alias = m.group(3) or ""
        return f"{embed}[[{new_target}{anchor}{alias}]]"

    return pattern.sub(sub, text)


def replace_in_file(path: Path, pattern: re.Pattern[str], new_target: str) -> bool:
    """Прочитать файл, заменить вхождения, записать обратно если изменилось.
    Возвращает True если файл изменён.
    """
    text = path.read_text(encoding="utf-8")
    new_text = replace_in_text(text, pattern, new_target)
    if new_text == text:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


def find_md_files() -> list[Path]:
    """Все .md под wiki/ и raw/, отсортированные."""
    files: list[Path] = []
    for root in (WIKI_ROOT, RAW_ROOT):
        if root.is_dir():
            files.extend(root.rglob("*.md"))
    return sorted(files)


def rename(old_path: Path, new_path: Path) -> tuple[int, list[Path]]:
    """Основная логика: валидация → обновление ссылок → mv.

    Возвращает (exit_code, changed_files).
    """
    if not old_path.is_file():
        print(f"Error: {old_path} does not exist or is not a file", file=sys.stderr)
        return 1, []
    if new_path.exists():
        print(f"Error: {new_path} already exists", file=sys.stderr)
        return 1, []

    try:
        old_mode = detect_mode(old_path)
        new_mode = detect_mode(new_path)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1, []

    if old_mode != new_mode:
        print(
            f"Error: cross-root rename not supported "
            f"({old_mode} → {new_mode}). Both paths must be under same root.",
            file=sys.stderr,
        )
        return 1, []

    old_target = link_target_for(old_path)
    new_target = link_target_for(new_path)

    pattern = build_pattern(old_target)
    changed: list[Path] = []
    for md in find_md_files():
        if replace_in_file(md, pattern, new_target):
            changed.append(md)

    new_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.rename(new_path)

    return 0, changed


def main() -> int:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <old_path> <new_path>", file=sys.stderr)
        return 1

    old_path = Path(sys.argv[1]).resolve()
    new_path = Path(sys.argv[2]).resolve()

    code, changed = rename(old_path, new_path)
    if code != 0:
        return code

    print(f"Moved: {old_path.relative_to(ROOT)} → {new_path.relative_to(ROOT)}")
    print(f"Updated wikilinks in {len(changed)} file(s)")
    for f in changed:
        print(f"  {f.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
