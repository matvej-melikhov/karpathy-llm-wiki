# llm-wiki

Obsidian-vault, управляемый через Claude Code. Реализует паттерн LLM Wiki — постоянная, накапливающаяся база знаний для Claude + Obsidian.

**Скиллы:** `/wiki`, `/ingest`, `/query`, `/lint`, `/save`, `/kn-map`

## Структура vault

| Путь | Назначение | Владелец | Семантика записи |
|---|---|---|---|
| `raw/` | Иммутабельные источники (md, pdf, docx, транскрипты) | пользователь | **immutable** для Claude |
| `raw/formats/` | Бинарные оригиналы (видео/аудио/изображения) | пользователь | immutable |
| `_attachments/` | Картинки и PDF для wiki-страниц | `ingest` | additive |
| `_templates/` | Шаблоны Obsidian Templater | пользователь | curated |
| `wiki/ideas/` | Концепции, механизмы, теории | `ingest` | additive + правка |
| `wiki/entities/` | Люди, статьи, библиотеки, модели | `ingest` | additive + правка |
| `wiki/domains/` | Навигационные хабы по областям (MOC) | `ingest` (порог N=10) | редко |
| `wiki/questions/` | Сохранённые ответы из `/save`, `/query` | `save`, `query` | additive |
| `wiki/index.md` | Каталог всех страниц | `ingest`, `save` | additive + правка |
| `wiki/log.md` | Хронологический журнал операций | все скиллы | **append-only** (сверху) |
| `wiki/cache.md` | Горячий кэш ~500 слов («где остановились») | `ingest`, `save`, `query` | **overwrite целиком** |
| `wiki/summary.md` | Обзор vault (счётчики, статус) | `ingest` | overwrite |
| `ARCHITECTURE.md` (repo root) | Design-doc структуры (этот документ — компактная сводка) | вручную | curated |
| `wiki/meta/embeddings.json` | Эмбеддинги для approx-lint | `bin/embed.py` | generated |
| `wiki/meta/lint-reports/` | `lint-state.json` + опц. отчёты | `lint` | generated |
| `wiki/meta/kn-maps/` | Снимки графа знаний | `bin/knowledge_map.py` | generated |
| `wiki/meta/dashboards/*.base` | Obsidian Bases для дашбордов | `obsidian-bases` | curated |
| `bin/` | Скрипты: `embed.py`, `lint.py`, `transcribe.py`, `knowledge_map.py`, setup | пользователь | curated |

**Семантика записи:**
- **immutable** — Claude никогда не правит
- **overwrite** — файл перезаписывается целиком (не append)
- **append-only** — только добавление, старое не правится
- **additive** — новые файлы создаются, существующие правятся точечно (Edit)
- **curated** — создано вручную, редко правится
- **generated** — derivable артефакт, можно безопасно удалить и пересчитать

Полная архитектура с потоками данных, инвариантами и зонами ответственности скиллов — `ARCHITECTURE.md` в корне репо.

## Как использовать

Положи источник в `raw/`, скажи Claude: «ingest [имя файла]».

Задай вопрос — Claude читает индекс, затем углубляется в релевантные страницы.

Запусти `/lint` каждые 10–15 ingest для поиска сирот и пробелов.

## Кросс-проектное использование

Чтобы обращаться к этой wiki из другого проекта Claude Code, добавь в CLAUDE.md того проекта:

```markdown
## База знаний wiki
Path: ~/path/to/llm-wiki

Когда нужен контекст, которого нет в этом проекте:
1. Сначала прочитай wiki/cache.md (недавний контекст, ~500 слов)
2. Если мало — wiki/index.md (полный каталог)
3. Если нужны детали области — wiki/domains/<имя>.md
4. Только потом отдельные wiki-страницы

НЕ читай wiki для общих вопросов и вещей, уже есть в файлах проекта.
```
