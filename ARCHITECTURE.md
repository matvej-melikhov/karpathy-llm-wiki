---
type: meta
title: "Архитектура llm-wiki"
updated: 2026-05-01
related:
  - "[[index]]"
  - "[[summary]]"
  - "[[LLM Wiki Pattern]]"
  - "[[Retrieval-Augmented Generation]]"
---

# Архитектура llm-wiki

Design-doc проекта: какие папки и файлы существуют, кто за что отвечает, какая семантика записи. Источник истины для скиллов и для кросс-проектных интеграций. Концептуальная мотивация — в [[LLM Wiki Pattern]] (vs [[Retrieval-Augmented Generation|RAG]]).

---

## 1. Слои хранения

Vault разделён на четыре слоя по жизненному циклу данных. Каждый слой имеет свою семантику записи и своих писателей.

### 1.1 Источники (immutable)

| Путь | Содержимое | Кто пишет | Кто читает |
|---|---|---|---|
| `raw/` | Источники: `.md`, `.pdf`, `.docx`, видео-транскрипты, URL-снимки. Один файл = один источник. | пользователь, `transcribe` (только конвертация бинарников из `raw/formats/`) | `ingest`, `transcribe` |
| `raw/formats/` | Бинарные оригиналы (видео `.mkv`, аудио, картинки), которые требуют конвертации в `raw/*.md` перед ingest. | пользователь | `transcribe` |
| `raw/meta/embeddings.json` | Эмбеддинги сырых источников (для dedup и approx-lint). | `bin/embed.py` | `ingest`, `lint` |
| `raw/meta/ingested.json` | Манифест dedup: какие источники уже обработаны (хеши, source_url, целевые wiki-страницы). | `ingest` | `ingest`, `transcribe` |
| `_attachments/` | Картинки и PDF, на которые ссылаются wiki-страницы через `![[...]]`. | `ingest` (при ingest изображений), пользователь | Obsidian, читатели |

**Инвариант:** Claude **никогда не редактирует существующие файлы в `raw/`**. Создание новых файлов допустимо только через `transcribe` при конвертации бинарников из `raw/formats/`. Если источник плохой — пользователь правит вручную. `_attachments/` — additive: новые файлы добавляются, старые остаются. `raw/meta/*.json` — generated артефакты, ими управляют скрипты и `ingest`.

### 1.2 Контент wiki (LLM synthesis)

| Путь | Содержимое | Семантика |
|---|---|---|
| `wiki/ideas/` | Концепции, механизмы, теории ([[RLHF]], [[YetiRank]], [[Decision Tree]], ...). | additive + правка |
| `wiki/entities/` | Люди, организации, статьи, библиотеки, модели ([[InstructGPT]], [[CatBoost]], [[Andrej Karpathy]], ...). | additive + правка |
| `wiki/domains/` | Навигационные хабы для области (MOC — map of content). Создаются при пороге N=10 тегов области. | редко |
| `wiki/questions/` | Сохранённые ответы и синтезы из `/save` и `/query`. | additive |

Каждая страница имеет frontmatter (`type`, `title`, `tags`, `domain`, `sources`, `related`, `status`) и связана wikilinks. Точное определение фронтматтера и шаблоны — в `_templates/`.

### 1.3 Инфраструктура (мета-навигация)

| Путь | Назначение | Семантика записи |
|---|---|---|
| `wiki/index.md` | Каталог всех страниц с одно-предложенными саммари. Точка входа для query/ingest. **Генерируется автоматически** из `summary:` во frontmatter каждой страницы. | generated |
| `wiki/log.md` | Хронологический журнал операций. Новые записи **сверху**. Не сжимается. | append-only |
| `wiki/cache.md` | Горячий кэш ~500 слов: «где остановились». Бюджет hard-cap 700 слов. | **overwrite целиком** |
| `wiki/summary.md` | Обзор vault (счётчики, домены, статус). | overwrite |
| `ARCHITECTURE.md` (repo root) | Этот файл — design-doc. **Лежит вне `wiki/`**, потому что `wiki/` — пользовательский контент, не коммитится в git, а design-doc должен быть частью репозитория и виден разработчикам после `git clone`. | редко |

**Семантика cache:** правила в [`wiki/SKILL.md`](file:.claude/skills/wiki/SKILL.md) (раздел «Дисциплина»). Кратко: 1 запись в «Последнее обновление», 6–10 буллетов в «Ключевые факты», sliding window 3–5 строк в «Недавние изменения». Старая история живёт в `log.md`.

### 1.4 Auto-generated (скрипты `bin/`)

| Путь | Что хранит | Кто генерирует |
|---|---|---|
| `raw/meta/embeddings.json` | Эмбеддинги сырых источников (для dedup при ingest и approx-lint). | `bin/embed.py` |
| `raw/meta/ingested.json` | Манифест dedup: source_url, хеши файлов, целевые wiki-страницы. Чтобы повторный ingest не запускал синтез заново. | `ingest` |
| `wiki/meta/embeddings.json` | Эмбеддинги всех wiki-страниц (≈7 MB на 50 страниц). | `bin/embed.py` |
| `wiki/meta/lint-reports/lint-state.json` | Текущее состояние lint (`open_issues`, `aggregate_hash`, `contradiction_candidates`). | `bin/lint.py` (= скилл `lint`) |
| `wiki/meta/lint-reports/lint-report-YYYY-MM-DD.md` | Человеко-читаемый отчёт (опц.). | `lint` (по запросу) |
| `wiki/meta/kn-maps/knowledge-map-YYYY-MM-DD.md` | Снимок графа знаний (плотность связей, кластеры). | `bin/knowledge_map.py` |
| `wiki/meta/dashboards/<Domain>.base`, `dashboard.base` | Obsidian Bases-файлы. Дефолтные шаблоны генерируются скриптом; ручные правки сохраняются. | `bin/gen_dashboards.py` (create-only); `obsidian-bases` (для нешаблонных Bases) |
| `wiki/index.md` | Каталог wiki: таблицы Ideas / Entities / Domains / Questions, формируются из `summary:` во frontmatter каждой страницы. Полная перезапись каждый Stop. | `bin/gen_index.py` |

**Семантика:** все эти артефакты **derivable** — могут быть пересчитаны из контента. Их безопасно удалять. В `.gitignore` обычно входит `embeddings.json` (большой бинарный JSON).

---

## 2. Семантика записи (cheat-sheet)

| Семантика | Описание | Где |
|---|---|---|
| **Immutable** | Существующие файлы не редактируются Claude'ом. Запись новых — только в узких случаях (см. ниже). | `raw/` (новые файлы только через `transcribe`) |
| **Overwrite** | Файл перезаписывается целиком при каждом обновлении. | `cache.md`, `summary.md`, `lint-state.json` |
| **Append-only** | Только добавление новых записей (обычно сверху). Старое не редактируется. | `log.md` |
| **Additive** | Создаются новые файлы; существующие правятся точечно (Edit, не Write). | `ideas/`, `entities/`, `domains/`, `questions/`, `_attachments/` |
| **Curated** | Создаётся вручную при setup, редко правится. | `_templates/`, `CLAUDE.md`, `ARCHITECTURE.md` |
| **Generated** | Производный артефакт; всегда можно пересчитать из контента. | `meta/embeddings.json`, `meta/lint-reports/`, `meta/kn-maps/` |

---

## 3. Зоны ответственности скиллов

Кто какой файл может писать. Это контракт — нарушение = баг скилла.

| Скилл | Пишет | Не пишет |
|---|---|---|
| `ingest` | `wiki/{ideas,entities,domains}/` (включая `summary:` во frontmatter), `wiki/{cache,log,summary}.md`, `_attachments/` | `raw/`, lint-state, `questions/`, **`wiki/index.md` (генерируется скриптом)** |
| `save` | `wiki/questions/` (включая `summary:`), `wiki/{cache,log}.md` | `ideas/`, `entities/` (это область ingest), lint-state, **`wiki/index.md`** |
| `query` | (опц.) `wiki/questions/` через делегирование на save, обновляет `cache.md` после значимых ответов | content-страницы напрямую |
| `lint` | **только** `wiki/meta/lint-reports/lint-state.json` (+ опц. отчёт) | content-файлы — все правки делает `ingest` по `open_issues` из lint-state |
| `wiki` | Роутер. Сам ничего не пишет, делегирует. | – |
| `transcribe` | `raw/<имя>.md` (результат конвертации `raw/formats/...`) | `wiki/`, `_attachments/` |
| `obsidian-bases` | `wiki/meta/dashboards/*.base` (только нешаблонные / разовые правки) | content |
| `defuddle` | возвращает markdown в stdout — фактическую запись в `raw/` делает пользователь или вызывающий скилл | – |

Скрипты `bin/`:

| Скрипт | Запись | Назначение |
|---|---|---|
| `bin/embed.py` | `wiki/meta/embeddings.json` | Обновляет эмбеддинги для approx-lint. **Запускается Stop-hook'ом** в `.claude/settings.json` после каждого turn'а — скиллы про это не знают и не вызывают вручную. Hash-skip пропускает неизменённые страницы. |
| `bin/lint.py` | `wiki/meta/lint-reports/lint-state.json` | Программные проверки (15 типов issues) + опц. `--approx` для embedding-based. |
| `bin/knowledge_map.py` | `wiki/meta/kn-maps/knowledge-map-*.md` | Снимок графа знаний. |
| `bin/transcribe.py` | `raw/<имя>.md` | Конвертация бинарных источников. |
| `bin/gen_dashboards.py` | `wiki/meta/dashboards/*.base` (только если файла нет) | Генерирует дефолтные дашборды для каждого `wiki/domains/*.md` и глобальный `dashboard.base`. **Запускается Stop-hook'ом** (async, ~100ms). Существующие `.base` не перезаписывает — ручные правки выживают. |
| `bin/gen_index.py` | `wiki/index.md` (полная перезапись) | Обходит `wiki/{ideas,entities,domains,questions}/`, читает `summary:` из frontmatter, формирует таблицы. Шапка зашита в скрипте. **Запускается Stop-hook'ом** (async, ~150ms на ~50 страниц). Полная перезапись каждый раз — index стабильно отражает текущее состояние frontmatter. |
| `bin/setup-vault.sh`, `bin/setup.sh` | initial scaffold | Однократно при создании vault. |

---

## 4. Поток данных: ingest

Канонический пайплайн. Подробности — в `.claude/skills/ingest/SKILL.md` и `references/synthesis-phases.md`.

```
raw/source.md
   │
   │  Phase 1: чтение источника
   ▼
   читает: wiki/cache.md → wiki/index.md → 3-5 релевантных страниц
   │
   │  Phase 2: карта знания (декомпозиция на units)
   │  Phase 3: гранулярность (новая страница vs обогащение)
   │  Phase 4: запись страниц (по шаблону из _templates/)
   ▼
   пишет: wiki/ideas/*, wiki/entities/*
   │
   │  Phase 5: связи (frontmatter + inline wikilinks)
   │  Phase 6: инфраструктура
   ▼
   обновляет: wiki/log.md (запись сверху), wiki/cache.md (overwrite),
              wiki/summary.md (counters). wiki/index.md обновится автоматически
              на Stop-hook'е через bin/gen_index.py из `summary:` во frontmatter.
   │
   │  Phase 7: domain proposal (если порог N=10 пройден)
   │  Phase 8: lint review (опц.)
   ▼
   bin/lint.py [--approx] → lint-state.json
   ingest применяет open_issues (auto-fix / ask / skip)
   │
   │  на завершении turn'а Claude — Stop hook:
   ▼
   bin/embed.py update → embeddings.json (вне ingest, для следующего turn'а)
```

---

## 5. Поток данных: query

```
вопрос пользователя
   │
   ▼
читает: wiki/cache.md (~500 слов, ~500 токенов)
   │
   │  если нашёл ответ → отвечает + обновляет cache при значимом обмене
   │  иначе:
   ▼
читает: wiki/index.md (~1000 токенов) → находит релевантные страницы
   │
   ▼
читает: 1-3 целевые страницы (`ideas/`, `entities/`, `domains/`)
   │
   │  опц. (deep): читает linked pages через wikilinks
   ▼
синтезирует ответ с цитатами
   │
   │  если ответ значимый:
   ▼
делегирует на save → wiki/questions/ + log + cache
```

**Бюджет токенов:** quick — только cache (~500), standard — cache+index+1-3 страницы (~2000), deep — +linked (~5000).

---

## 6. Ключевые инварианты

1. **Существующие файлы в `raw/` не редактируются** Claude'ом. Создание новых файлов допустимо только через `transcribe` при конвертации бинарников из `raw/formats/`. Удаление и переименование источников — только пользователь.
2. **`cache.md` — overwrite.** Не append. Старая история — в `log.md`.
3. **`log.md` — append-only.** Новые записи сверху, старое не правится.
4. **Lint не правит content.** Все фиксы — через `ingest` по `lint-state.json::open_issues`.
5. **Один концепт = одна страница.** Перед созданием новой страницы `ingest` обязан проверить `index.md` и `embeddings.json` на дубликаты (semantic dedup).
6. **Каждая страница имеет источник.** Frontmatter `sources:` с путём в `raw/...` или URL. Страница без источника — баг.
7. **Wikilinks — единственный способ связи.** Никаких сырых путей к страницам в теле текста.
8. **Domain создаётся по порогу.** Тег с N≥10 страницами → предложение создать `domains/<name>.md`. Меньше — просто тег.
9. **Provenance в `log.md`.** Каждый ingest/save оставляет запись в `log.md` с указанием источника, созданных и обновлённых страниц.

---

## 7. Кросс-проектное использование

Из другого Claude Code проекта можно читать эту wiki в режиме «лёгкого справочника». Иерархия чтения по возрастанию стоимости токенов:

1. `wiki/cache.md` — ~500 токенов, последний контекст.
2. `wiki/index.md` — ~1000 токенов, полный каталог.
3. `wiki/domains/<имя>.md` — ~500-1000 токенов, обзор области.
4. Конкретные `wiki/ideas/<страница>.md` или `wiki/entities/<страница>.md` — 100-300 токенов каждая.

Подробная инструкция для встраивания — в `CLAUDE.md` корня проекта.

---

## 8. Что НЕ является частью архитектуры

- `PROJECT.md` (≈90 KB) — это рабочий документ для ВКР, описывает **состояние** проекта (история этапов, технические детали реализации). Не является частью runtime-структуры vault. Может быть удалён без последствий для работы Claude.
- `.claudian/`, `.claude/`, `.obsidian/`, `.git/` — служебные директории внешних систем, не относящиеся к данным vault.
- `bin/tests/` — тесты для скриптов `bin/`. Опциональны для работы vault.

---

## 9. Git-политика: что коммитим, что нет

В репо коммитится **только инфраструктура** — то, что одинаково у всех пользователей этого vault. **Контент per-user — не коммитится.** Принцип: разработчик клонирует репо, получает рабочий vault-каркас, наполняет его собственными источниками и страницами.

| Категория | Путь | В git |
|---|---|---|
| **Документация / design** | `CLAUDE.md`, `ARCHITECTURE.md`, `README.md` | ✓ |
| **Скиллы и конфиг Claude** | `.claude/skills/`, `.claude/settings.json` | ✓ |
| **Скрипты** | `bin/*.py`, `bin/*.sh`, `bin/requirements.txt`, `bin/tests/` | ✓ |
| **Шаблоны** | `_templates/*.md` | ✓ |
| **Obsidian-конфиг** | `.obsidian/` (кроме `workspace.json`) | ✓ (shared plugin set + visual config) |
| **Источники** | `raw/` | ✗ per-user |
| **Wiki-контент** | `wiki/ideas/`, `wiki/entities/`, `wiki/domains/`, `wiki/questions/` | ✗ per-user |
| **Wiki-инфра-файлы** | `wiki/index.md`, `wiki/log.md`, `wiki/cache.md`, `wiki/summary.md` | ✗ per-user (производные от контента) |
| **Аттачменты** | `_attachments/` | ✗ per-user |
| **Generated** | `wiki/meta/embeddings.json`, `wiki/meta/lint-reports/`, `wiki/meta/kn-maps/` | ✗ derivable |
| **Workspace UI** | `.obsidian/workspace.json`, `.env`, `.DS_Store` | ✗ (в `.gitignore`) |

`.gitignore` сейчас покрывает только секреты и macOS/IDE-мусор. Дисциплина «не коммитить контент» поддерживается **вручную**: при `git add` явно указывать файлы из инфра-зоны, не использовать `git add .` или `git add -A`.

`ARCHITECTURE.md` лежит в repo root, а не в `wiki/`, именно по этой причине: design-doc — часть инфраструктуры, должен быть виден после `git clone`, до того как Claude или Obsidian что-либо инициализируют в vault.

---

## 10. Эволюция

Документ описывает текущее состояние (2026-05-01). Изменения архитектуры:

- При добавлении нового слоя/папки — обновить разделы 1, 2, 3, синхронизировать с `CLAUDE.md`.
- При изменении семантики записи существующего файла — обновить раздел 2 и владеющий скилл.
- При появлении нового скилла — добавить строку в раздел 3.

См. также: [[index]] (каталог), [[summary]] (счётчики), [[LLM Wiki Pattern]] (концепция), [[Retrieval-Augmented Generation|RAG]] (то, чему противопоставлена эта архитектура).
