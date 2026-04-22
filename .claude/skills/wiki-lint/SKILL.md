---
name: wiki-lint
description: >
  Read-only ревьюер Obsidian wiki-vault. Находит страницы-сироты, мёртвые wikilinks,
  устаревшие утверждения, отсутствующие перекрёстные ссылки, пробелы во frontmatter,
  пустые секции, нарушения схемы. Записывает результат в `wiki/meta/lint-state.json`,
  включая структурированный список `open_issues` для применения wiki-ingest. Кэширует
  состояние через aggregate hash, чтобы не перепроверять неизменную wiki.
  Триггеры: /lint, "lint", "проверка здоровья", "очисти wiki",
  "проверь wiki", "найди сирот", "wiki audit".
---

# wiki-lint: ревьюер wiki

Lint **только читает** wiki и записывает структурированный отчёт. **Никаких правок в content-файлы (`wiki/ideas/`, `wiki/entities/` и т.д.)** — фиксы делает `wiki-ingest` по этому отчёту.

Единственный файл, который lint имеет право писать — `wiki/meta/lint-state.json`.

---

## Команда и флаги

| Команда | Поведение |
|---|---|
| `/lint` | Skip-check по hash. Если wiki не менялась И нет open_issues — пропуск. Иначе full audit. |
| `/lint --force` | Игнорировать skip-check, всегда full audit. |

После audit:
- если есть open_issues — список выводится пользователю
- предлагается: "Применить через `/ingest --fix`?"

---

## Skip-check (агрегатный hash)

`wiki/meta/lint-state.json` хранит результат последнего audit:

```json
{
  "wiki_hash": "<sha256 от конкатенации всех wiki/**/*.md>",
  "last_audit": "2026-04-29T15:45:00",
  "files_checked": 11,
  "open_issues": [
    {
      "type": "dead-link",
      "where": "wiki/ideas/RLHF.md",
      "what": "[[SFT]]",
      "context": "упомянута на строке 32"
    }
  ]
}
```

**При входе в `/lint`:**

1. Если `wiki/meta/lint-state.json` отсутствует — пропустить skip-check, идти на full audit.
2. Прочитать `lint-state.json`, получить `wiki_hash` и `open_issues`.
3. Посчитать текущий `wiki_hash`:
   ```bash
   find wiki -name '*.md' \
     -not -path 'wiki/meta/lint-state.json' \
     -not -path 'wiki/meta/lint-report-*.md' \
     | sort | xargs cat | sha256sum | cut -d' ' -f1
   ```
   (Сортировка чтобы порядок не влиял; конкатенация всех тел; один итоговый sha256.)
4. Сравнить с сохранённым `wiki_hash`:
   - Совпадает И `open_issues` пуст → пропуск:
     ```
     Wiki не менялась с последнего audit (2026-04-29 15:45). Чисто. Пропускаю.
     ```
   - Совпадает И `open_issues` не пуст → не аудитим заново, **показываем сохранённые `open_issues`** + предлагаем починить через `/ingest --fix`.
   - Не совпадает → full audit.

С `--force` шаги 1–4 пропускаются.

---

## Full audit

Полный обход wiki, без правок. По завершении:

1. Записать `wiki/meta/lint-state.json` с актуальным `wiki_hash`, `last_audit`, `files_checked`, новым списком `open_issues`.
2. Вывести пользователю краткую сводку + список issues.
3. Предложить: "Применить безопасные правки и пройтись по требующим решения через `/ingest --fix`?"

---

## Категории issues

Каждый issue в `open_issues` имеет поле `type` — категория. По типу `wiki-ingest` решает, как с ним поступить.

### Auto-fix (ingest правит молча)

Детерминистические нарушения схемы, единственный очевидный fix:

| `type` | Условие | Структура issue |
|---|---|---|
| `status-not-in-enum` | `status` не из `evaluation/in-progress/ready` | `{type, where, value, fix: "in-progress"}` |
| `status-on-entity` | у `type: entity` присутствует поле `status` | `{type, where}` |
| `legacy-field` | поля старой схемы (`title`, `complexity`, `first_mentioned`) на не-meta | `{type, where, field}` |
| `lowercase-tags` | теги в lowercase или со смешанным регистром аббревиатур | `{type, where, tags: [...]}` |
| `inline-tags` | `tags: [a, b]` инлайн вместо блочного YAML | `{type, where}` |
| `raw-link-with-extension` | `[[raw/X.md]]` вместо `[[raw/X]]` | `{type, where, link}` |
| `raw-ref-in-body` | упоминание `[[raw/...]]` в теле страницы | `{type, where, link, line}` |
| `empty-sources-section` | секция `## Источники`/`## Источники упоминания` содержит только `[[raw/...]]` | `{type, where, section}` |

### Ask user (ingest спрашивает решение)

Требует суждения:

| `type` | Условие | Структура issue |
|---|---|---|
| `dead-link` | wikilink на несуществующую страницу | `{type, where, what, context}` |
| `orphan` | страница без входящих wikilinks | `{type, where}` |
| `missing-concept` | концепция упомянута в ≥3 страницах без своей wiki-страницы | `{type, term, mentioned_in: [...]}` |
| `contradiction` | противоречие между утверждениями двух страниц | `{type, page_a, page_b, claim}` |
| `outdated-claim` | утверждение в `[[A]]` потенциально опровергнуто `[[B]]` | `{type, where, claim, conflicts_with}` |

### Skip (только записываем, не спрашиваем)

Информационные флаги — пользователю не задаём вопрос:

| `type` | Условие |
|---|---|
| `empty-section` | секция без контента (может быть намеренно) |
| `style-nit` | не декларативное настоящее, отсутствие линка на не-ключевую сущность |

---

## Проверки

| # | Проверка | Категория |
|---|---|---|
| 1 | Сирота (нет входящих wikilinks) | ask |
| 2 | Мёртвая ссылка | ask |
| 3 | Устаревшее утверждение | ask |
| 4 | Пропущенная концепция (≥3 упоминания) | ask |
| 5 | Противоречие | ask |
| 6 | `status` вне enum | auto-fix |
| 7 | `status` на entity | auto-fix |
| 8 | Поля старой схемы (`title`/`complexity`/`first_mentioned`) | auto-fix |
| 9 | Tags casing (lowercase аббревиатур) | auto-fix |
| 10 | Tags inline-формат | auto-fix |
| 11 | `[[raw/X.md]]` (с расширением) в `sources` | auto-fix |
| 12 | Raw-refs в теле страницы | auto-fix |
| 13 | "Sources"-секция с одним raw | auto-fix |
| 14 | Пустые секции | skip |
| 15 | Стилистические нарушения | skip |
| 16 | Устаревшие записи в `index.md` (ведут на удалённые страницы) | ask |

---

## Конвенции

| Элемент | Конвенция | Пример |
|---|---|---|
| Имена файлов | Title Case с пробелами | `Reward Model.md` |
| Папки | lowercase | `wiki/ideas/`, `wiki/entities/` |
| Теги | аббревиатуры заглавными, обычные слова с заглавной первой буквы | `ML`, `RL`, `Alignment` |
| Wikilinks | точно совпадают с именем файла | `[[Reward Model]]` |
| `[[raw/...]]` | без расширения `.md`, только в `sources` frontmatter | `[[raw/RLHF]]` |

Имена файлов уникальны по всему vault — wikilinks без путей работают только при уникальности.

---

## Что lint не делает

- Не правит content-файлы. Никогда.
- Не создаёт стаб-страницы. Не предлагает создавать в `auto-fix` категории — это всегда `ask`.
- Не удаляет файлы. Сирот только флагает.
- Не разрешает противоречия. Только описывает.

Все правки делает `wiki-ingest` (через Phase 8 после синтеза или через `/ingest --fix` вне цикла).
