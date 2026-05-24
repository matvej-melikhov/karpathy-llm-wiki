# Архитектура llm-wiki

Design-doc проекта: какие папки и файлы существуют, кто за что отвечает, какая семантика записи. Источник истины для скиллов и для кросс-проектных интеграций.

---

## 1. Слои хранения

Vault разделён на четыре слоя по жизненному циклу данных. Каждый слой имеет свою семантику записи и своих писателей.

### 1.1 Источники (immutable)

| Путь                       | Содержимое                                                                                                                                     | Кто пишет                                                                    | Кто читает             |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- | ---------------------- |
| `raw/`                     | Источники: `.md`, `.pdf`, `.docx`, видео-транскрипты, URL-снимки. Один файл = один источник.                                                   | пользователь, `transcribe` (только конвертация бинарников из `raw/formats/`) | `ingest`, `transcribe` |
| `raw/formats/`             | Бинарные оригиналы (видео `.mkv`, аудио, картинки).                                                                                            | пользователь                                                                 | `transcribe`           |
| `raw/brainstorm/`          | Транскрипты brainstorm-сессий. Один файл = одна сессия.                                                                                        | `brainstorm`                                                                 | `bin/embed.py`, `lint` |
| `_attachments/`            | Картинки и PDF, на которые ссылаются wiki-страницы через `![[...]]`.                                                                           | `ingest` (при ingest изображений), пользователь                              | Obsidian, пользователь |

### 1.2 Контент wiki (LLM synthesis)

| Путь              | Содержимое                                                                                                         | Семантика         |
| ----------------- | ------------------------------------------------------------------------------------------------------------------ | ----------------- |
| `wiki/ideas/`     | Концепции, механизмы, теории (RLHF, YetiRank, Decision Tree, ...).                                                 | additive + правка |
| `wiki/entities/`  | Люди, организации, статьи, библиотеки, модели (InstructGPT, CatBoost, Andrej Karpathy, ...).                       | additive + правка |
| `wiki/domains/`   | Навигационные хабы для области (MOC — map of content). Создаются при пороге N=10 тегов области.                    | additive + правка |
| `wiki/questions/` | Сохранённые ответы и синтезы из `/save` и `/query`.                                                                | additive          |
| `wiki/minds/`     | Авторские размышления (Mind Mapping). Рождаются в `/brainstorm`-сессиях. Свободная форма, без обязательных секций. | additive + правка |

### 1.3 Навигация и состояние

| Путь              | Назначение                                                                                                                                       | Семантика записи |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------- |
| `wiki/index.md`   | Индекс всех страниц с кратким саммари. Точка входа для query/ingest. **Генерируется автоматически** по `summary` во frontmatter каждой страницы. | generated        |
| `wiki/log.md`     | Хронологический журнал операций. Новые записи **сверху**. Не сжимается.                                                                          | append-only      |
| `wiki/cache.md`   | Горячий кэш ~500 слов: актуальный контекст. Бюджет hard-cap 700 слов.                                                                            | **curated rewrite** (см. CLAUDE.md §Кэш контекста) |
| `wiki/summary.md` | Обзор vault (счётчики, домены, статус).                                                                                                          | overwrite        |

### 1.4 Метаданные (auto-generated)

| Путь                                                   | Что хранит                                                        | Кто генерирует                                                                  |
| ------------------------------------------------------ | ----------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `raw/meta/embeddings.json`                             | Эмбеддинги сырых источников.                                      | `bin/embed.py`                                                                  |
| `raw/meta/ingested.json`                               | Манифест dedup. Чтобы повторный ingest не запускал синтез заново. | `ingest`                                                                        |
| `wiki/meta/embeddings.json`                            | Эмбеддинги wiki-страниц.                                          | `bin/embed.py`                                                                  |
| `wiki/meta/lint-reports/lint-state.json`               | Последнее состояние lint.                                         | `bin/static_lint.py`                                                            |
| `wiki/meta/lint-reports/lint-report-YYYY-MM-DD.md`     | Человеко-читаемый отчёт о результатах lint.                       | `lint`                                                                          |
| `_attachments/snapshot-YYYY-MM-DD.html`                | Dated UMAP-карта (Cytoscape, preset-layout) на дату snapshot.     | `bin/knowledge_map.py` (`/snapshot`)                                            |
| `_attachments/snapshot-graph-YYYY-MM-DD.html`          | Dated force-directed граф топологии (fcose-layout).               | `bin/knowledge_map.py` (`/snapshot`)                                            |
| `wiki/meta/vault-explorer.html`                        | Живой дашборд vault: метрики, time-series, pulse, distributions, карты, инсайты. Единственная точка входа. | `bin/update_dashboard.py` (после каждого turn'а через Stop-hook)                |
| `wiki/meta/snapshots/history.jsonl`                    | Append-only журнал метрик по turn'ам (delta + триггер).           | `bin/update_dashboard.py`                                                       |
| `wiki/meta/snapshots/heavy.json`                       | Sankey + treemap данные + LLM-инсайты к 5 картам + пути к dated HTML. | `bin/knowledge_map.py` пишет данные, `/snapshot` skill пишет инсайты            |
| `wiki/meta/vendor/*.js`                                | Vendored Chart.js + плагины (зеркало `bin/vendor/`).              | `bin/update_dashboard.py::ensure_vendor()`                                      |
| `wiki/meta/dashboards/<Domain>.base`, `dashboard.base` | Obsidian Bases-файлы.                                             | `bin/gen_dashboards.py` (create-only); `obsidian-bases` (для нешаблонных Bases) |

**Семантика:** все эти артефакты **derivable** — могут быть пересчитаны из контента. Их безопасно удалять.

---

## 2. Зоны ответственности скиллов

Это контракт — нарушение = баг скилла.

| Скилл            | Пишет                                                                                               |
| ---------------- | --------------------------------------------------------------------------------------------------- |
| `ingest`         | `wiki/{ideas,entities,domains}/`, `wiki/{cache,log,summary}.md`, `_attachments/`                    |
| `save`           | `wiki/questions/`, `wiki/{cache,log}.md`                                                            |
| `brainstorm`     | `wiki/minds/`, `raw/brainstorm/<date>-<slug>.md`, `wiki/{cache,log}.md`                             |
| `study`          | через делегирование на `/save` — `wiki/{ideas,questions}/` с `provenance: study`, `wiki/{cache,log}.md` |
| `query`          | `wiki/questions/` (опц.) через делегирование на save, обновляет `cache.md` после значимых ответов   |
| `edge`           | read-only — только статистика на stdout                                                             |
| `snapshot`       | `_attachments/snapshot-*.html`, `_attachments/snapshot-graph-*.html`, `wiki/meta/snapshots/heavy.json` (sankey + treemap + insights); триггерит `bin/update_dashboard.py` для refresh `vault-explorer.html` |
| `lint`           | `wiki/meta/lint-reports/lint-state.json` (+ опц. отчёт), content-файлы                              |
| `help`           | read-only — справка по скиллам из `.claude/skills/*/SKILL.md`                                       |
| `transcribe`     | `raw/<имя>.md` (транскрибация из `raw/formats/...`)                                                 |
| `obsidian-bases` | `wiki/meta/dashboards/*.base` (только нешаблонные / разовые правки)                                 |
| `defuddle`       | возвращает markdown в stdout — фактическую запись в `raw/` делает пользователь или вызывающий скилл |

## 3. Скрипты `bin/`

| Скрипт                               | Запись                                                  | Назначение                                                                        |
| ------------------------------------ | ------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `bin/embed.py`                       | `wiki/meta/embeddings.json`                             | Обновляет эмбеддинги страниц.                                                     |
| `bin/static_lint.py`                 | `wiki/meta/lint-reports/lint-state.json`                | Статические проверки и embedding-based lint wiki-страниц.                         |
| `bin/knowledge_map.py`               | `_attachments/snapshot-*.html`, `_attachments/snapshot-graph-*.html`, `wiki/meta/snapshots/heavy.json` | UMAP + force-graph + sankey/treemap данные. Тяжёлый контур, вызывается через `/snapshot` (раз в сутки). |
| `bin/update_dashboard.py`            | `wiki/meta/vault-explorer.html`, `wiki/meta/snapshots/history.jsonl`, `wiki/meta/vendor/*.js` | Лёгкий контур: числовые метрики, distributions, pulse, time-series, word cloud, health score. ~1с на 60 страниц. |
| `bin/transcribe.py`                  | `raw/<имя>.md`                                          | Конвертация бинарных источников.                                                  |
| `bin/gen_dashboards.py`              | `wiki/meta/dashboards/*.base` (только если файла нет)   | Дефолтные дашборды по `wiki/domains/*.md` + глобальный `dashboard.base`.          |
| `bin/gen_index.py`                   | `wiki/index.md` (полная перезапись)                     | Сборка `index.md` по `summary` во frontmatter страниц.                            |
| `bin/rename_wiki_page.py`            | wiki-/raw-страница (rename/move) + все wikilinks на неё | Rename/move страницы с обновлением всех wikilinks.                                |
| `bin/check_dashboard.py`             | — (read-only)                                           | Headless-проверка `vault-explorer.html` через Playwright. Dev-инструмент.         |
| `bin/init.sh`                        | initial scaffold                                        | Интерактивный wizard: зависимости, `.env`, Obsidian-конфиг, переинициализация git. Однократно после клона. |

`embed.py`, `gen_index.py`, `gen_dashboards.py`, `update_dashboard.py` запускаются автоматически Stop-hook'ом после каждого turn'а — скиллы их не вызывают. `knowledge_map.py` — только через `/snapshot`.

---

## 4. Кросс-проектное использование

Из другого agent-coding проекта можно читать эту wiki в режиме «справочника». Иерархия чтения по возрастанию стоимости токенов:

1. `wiki/cache.md` — ~500 токенов, последний контекст.
2. `wiki/domains/<имя>.md` — ~500-1000 токенов, обзор области.
3. `wiki/index.md` — ~1000-5000 токенов, полный каталог.
4. Конкретные `wiki/ideas/<страница>.md` или `wiki/entities/<страница>.md` — 100-300 токенов каждая.

Подробная инструкция для встраивания — в `CLAUDE.md`.
