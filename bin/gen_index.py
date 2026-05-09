#!/usr/bin/env python3
"""Generate wiki/index.md from page frontmatter.

Обходит wiki/{ideas,entities,domains,questions}/, читает поле `summary:` из
frontmatter каждой страницы, формирует таблицы по типам. Шапка зашита в
скрипте. Полный index.md перезаписывается каждый запуск (idempotent).

Запускается из Stop-hook'а в .claude/settings.json.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "wiki" / "index.md"

SECTIONS = [
    ("Ideas", "ideas"),
    ("Entities", "entities"),
    ("Domains", "domains"),
    ("Questions", "questions"),
]

HEADER = """\
---
type: meta
title: "Индекс wiki"
updated: {date}
---

# Индекс знаний

База знаний. Заполняется через `/ingest`. Эта страница автоматически
генерируется `bin/gen_index.py` из поля `summary:` во frontmatter каждой
страницы. Не редактируй вручную — изменения затрутся при следующем
Stop-hook'е.

## Структура

- `ideas/` — синтезированные концепции, механизмы, теории
- `entities/` — синтезированные сущности (люди, организации, продукты, статьи, модели)
- `questions/` — синтезы на основе вопросов пользователя
- `domains/` — навигационные хабы (MOC)
- `meta/` — инфраструктура (dashboard, lint-отчёты)

## Служебное

- [[log|Журнал операций]]
- [[cache|Кэш контекста]]
- [[summary|Обзор wiki]]
- [Архитектура vault](../ARCHITECTURE.md)
"""

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
SUMMARY_RE = re.compile(r"^summary:\s*(.+?)\s*$", re.MULTILINE)


def read_summary(path: Path) -> str | None:
    """Extract `summary:` from frontmatter. Returns None if missing or empty."""
    text = path.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    fm = m.group(1)
    s = SUMMARY_RE.search(fm)
    if not s:
        return None
    raw = s.group(1).strip()
    if (raw.startswith("'") and raw.endswith("'")) or (
        raw.startswith('"') and raw.endswith('"')
    ):
        raw = raw[1:-1]
    raw = raw.replace("''", "'")
    return raw or None


def collect_section(folder: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    d = ROOT / "wiki" / folder
    if not d.is_dir():
        return out
    for md in sorted(d.glob("*.md")):
        summary = read_summary(md)
        if summary is None:
            summary = "_(нет summary в frontmatter)_"
        out.append((md.stem, summary))
    return out


def render_section(title: str, rows: list[tuple[str, str]]) -> str:
    if not rows:
        return f"## {title}\n\n| Страница | Суть |\n|---|---|\n\n_Пусто._\n"
    lines = [f"## {title}", "", "| Страница | Суть |", "|---|---|"]
    for name, summary in rows:
        lines.append(f"| [[{name}]] | {summary} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parts = [HEADER.format(date=date.today().isoformat())]
    for title, folder in SECTIONS:
        rows = collect_section(folder)
        parts.append("")
        parts.append(render_section(title, rows))
    new_text = "\n".join(parts)
    if INDEX.exists() and INDEX.read_text(encoding="utf-8") == new_text:
        return 0
    INDEX.write_text(new_text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
