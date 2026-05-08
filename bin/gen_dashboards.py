#!/usr/bin/env python3
"""Generate Obsidian Bases dashboards.

Создаёт `.base` файлы в `wiki/meta/dashboards/`:
- per-domain дашборд для каждой страницы в `wiki/domains/`
- глобальный `dashboard.base`

Идемпотентно: существующие файлы НЕ перезаписываются. Безопасно для запуска
из Stop-хука после каждого turn'а.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOMAINS_DIR = ROOT / "wiki" / "domains"
DASHBOARDS_DIR = ROOT / "wiki" / "meta" / "dashboards"

DOMAIN_TEMPLATE = """\
filters:
  and:
    - file.inFolder("wiki/")
    - file.hasLink("{name}")
    - not:
        - file.inFolder("wiki/domains/")
        - file.inFolder("wiki/meta/")
views:
  - type: table
    name: Все {abbr}-страницы
    groupBy:
      property: type
      direction: ASC
    order:
      - file.name
      - type
      - status
      - tags
      - updated
"""

GLOBAL_TEMPLATE = """\
filters:
  and:
    - file.ext == "md"
    - or:
        - file.inFolder("wiki/ideas")
        - file.inFolder("wiki/entities")
        - file.inFolder("wiki/questions")
        - file.inFolder("wiki/domains")
formulas:
  age_days: (now() - file.mtime).days.round(0)
properties:
  type:
    displayName: Type
  status:
    displayName: Status
  updated:
    displayName: Updated
  domain:
    displayName: Domain
  tags:
    displayName: Tags
  formula.age_days:
    displayName: Возраст (дней)
views:
  - type: table
    name: Recent Activity
    order:
      - file.name
      - type
      - status
      - domain
      - updated
      - formula.age_days
    sort:
      - property: domain
        direction: DESC
  - type: table
    name: Ideas
    filters:
      and:
        - file.inFolder("wiki/ideas")
    order:
      - file.name
      - status
      - domain
      - tags
      - updated
  - type: table
    name: Entities
    filters:
      and:
        - file.inFolder("wiki/entities")
    groupBy:
      property: entity_type
      direction: ASC
    order:
      - file.name
      - entity_type
      - status
      - domain
      - updated
  - type: list
    name: Questions
    filters:
      and:
        - file.inFolder("wiki/questions")
    order:
      - file.name
      - status
      - answer_quality
      - updated
  - type: list
    name: Domains
    filters:
      and:
        - file.inFolder("wiki/domains")
    order:
      - file.name
      - updated
  - type: list
    name: Needs Development
    filters:
      and:
        - or:
            - status == "evaluation"
            - status == "in-progress"
    order:
      - file.name
      - type
      - status
      - updated
"""


def abbr_of(name: str) -> str:
    """First letter of each word, uppercased: 'Machine Learning' -> 'ML'."""
    return "".join(w[0].upper() for w in name.split() if w)


def main() -> int:
    if not DOMAINS_DIR.is_dir():
        return 0
    DASHBOARDS_DIR.mkdir(parents=True, exist_ok=True)

    created: list[str] = []

    for md in sorted(DOMAINS_DIR.glob("*.md")):
        name = md.stem
        target = DASHBOARDS_DIR / f"{name}.base"
        if target.exists():
            continue
        target.write_text(
            DOMAIN_TEMPLATE.format(name=name, abbr=abbr_of(name)),
            encoding="utf-8",
        )
        created.append(target.name)

    global_target = DASHBOARDS_DIR / "dashboard.base"
    if not global_target.exists():
        global_target.write_text(GLOBAL_TEMPLATE, encoding="utf-8")
        created.append(global_target.name)

    if created:
        print(f"Created: {', '.join(created)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
