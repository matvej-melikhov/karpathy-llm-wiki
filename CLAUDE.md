# llm-wiki

Obsidian-vault, управляемый через Claude Code. Реализует паттерн LLM Wiki — постоянная, накапливающаяся база знаний для Claude + Obsidian.

**Скиллы:** `/wiki`, `/ingest`, `/query`, `/lint`, `/save`, `/kn-map`, `/transcribe`, `/defuddle`, `/obsidian-bases`, `/obsidian-markdown`

## Структура vault

| Путь | Назначение | Владелец | Режим записи Claude |
|---|---|---|---|
| `raw/` | Источники (md, pdf, docx, транскрипты) | пользователь, `transcribe` (только конвертация из `raw/formats/`) | create |
| `raw/formats/` | Бинарные оригиналы (видео/аудио/изображения) | пользователь | read-only |
| `raw/meta/*` | Эмбеддинги источников, dedup-манифест | `bin/embed.py`, `ingest` | generated |
| `_attachments/` | Картинки и PDF для wiki-страниц | `ingest` | create |
| `_templates/` | Шаблоны Obsidian Templater | пользователь | read-only |
| `wiki/ideas/` | Концепции, механизмы, теории | `ingest` | create + edit |
| `wiki/entities/` | Люди, статьи, библиотеки, модели | `ingest` | create + edit |
| `wiki/domains/` | Навигационные хабы по областям (MOC) | `ingest` (порог N=10) | create + edit |
| `wiki/questions/` | Сохранённые ответы из `/save`, `/query` | `save`, `query` | create + edit |
| `wiki/index.md` | Каталог всех страниц (генерируется из `summary:` во frontmatter) | `bin/gen_index.py` | generated |
| `wiki/log.md` | Хронологический журнал операций | все скиллы | **append** (сверху) |
| `wiki/cache.md` | Горячий кэш ~500 слов («где остановились») | `ingest`, `save`, `query` | **overwrite** |
| `wiki/summary.md` | Обзор vault (счётчики, статус) | `ingest` | overwrite |
| `wiki/meta/*` | Эмбеддинги, lint-state, kn-maps, дашборды | `bin/*`, `lint`, `gen_dashboards.py` | generated |

**Режим записи Claude:**
- **read-only** — Claude не пишет (источники, шаблоны, скрипты)
- **create** — создаёт новые файлы, существующие не правит
- **create + edit** — создаёт и точечно правит (Edit, не перезапись)
- **append** — только добавляет, старое не правится
- **overwrite** — перезаписывает целиком при каждом обновлении
- **generated** — derivable артефакт, можно безопасно удалить и пересчитать

Скрипты в `bin/` (`embed.py`, `static_lint.py`, `transcribe.py`, `knowledge_map.py`, `gen_dashboards.py`, `gen_index.py`, `rename_wiki_page.py`, setup) — пользовательский код, в таблице vault не учитываются.

**Rename/move страниц — только через `bin/rename_wiki_page.py <old> <new>`.** Прямой `mv` или `Write` на новый путь ломают wikilinks. Подробности — `ARCHITECTURE.md` §6, инвариант 10. Полная архитектура с потоками данных, инвариантами и зонами ответственности скиллов — `ARCHITECTURE.md` в корне репо. Этот файл (CLAUDE.md) — компактная сводка для каждой сессии.

## Как использовать

Положи источник в `raw/`, скажи Claude: «ingest [имя файла]».

Задай вопрос — Claude читает индекс, затем углубляется в релевантные страницы.

Запусти `/lint` каждые 10–15 ingest для поиска сирот и пробелов.

## Кросс-проектное использование

См. `ARCHITECTURE.md` §7 — инструкция по подключению этой wiki из другого Claude Code проекта.
