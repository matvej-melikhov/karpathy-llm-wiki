# Интеграция с wiki и ingest

Как восстановленный `.md` становится источником для синтеза знаний.

---

## Frontmatter восстановленного файла

Каждый `raw/<stem>.md` получает frontmatter:

```yaml
---
source_type: pdf
original_file: raw/formats/paper.pdf
restored: true
restored_at: 2026-05-01T10:30:00
pages: 26
---
```

Этот файл — обычный `.md`-источник. Он читается `ingest` как любой другой файл из `raw/`.

---

## `sources:` в wiki-страницах

При синтезе из транскрипта в frontmatter wiki-страниц ставится только
ссылка на восстановленный `.md`:

```yaml
sources:
  - "[[raw/paper]]"     # ← восстановленный .md, без расширения
```

**Не ставить** ссылку на бинарный оригинал `[[raw/formats/paper.pdf]]` —
он не рендерится в Obsidian. Если reviewer хочет проверить оригинал —
он откроет `raw/paper.md` и увидит там `original_file: raw/formats/paper.pdf`.

---

## Запуск transcribe из ingest

Когда `/ingest` получает файл с расширением `.pdf`, `.docx` (или находит
такой файл в `raw/formats/`), он:

1. Вызывает `transcribe` скилл для генерации `.md`
2. Дожидается завершения
3. Продолжает с `raw/<stem>.md` как обычным источником

Routing-строка в `ingest/SKILL.md`:

| Источник | Что читать |
|---|---|
| PDF/DOCX в `raw/formats/` или `raw/` | сначала `/transcribe <file>` → затем synthesis |

---

## Дедуп на уровне transcribe

`raw/meta/ingested.json` — зона ответственности `/ingest`, не `/transcribe`.

Transcribe использует простой файловый дедуп: если `raw/<stem>.md` уже существует и новее оригинала — skip:

```
raw/formats/paper.pdf   mtime: 2026-05-01 10:00
raw/paper.md            mtime: 2026-05-01 11:00  → skip (транскрипт актуален)
```

Флаг `--force` обходит проверку.

При запуске `/ingest raw/paper.md` — ingest записывает обычную запись в `raw/meta/ingested.json` для `raw/paper.md` (как для любого .md источника). Из какого PDF получен транскрипт — видно из frontmatter `.md` файла (`source: raw/formats/paper.pdf`).

---

## Изображения в wiki

Изображения, извлечённые при Step 1, находятся в `_attachments/`.
В восстановленном `.md` они встроены через Obsidian embeds:

```markdown
## Архитектура

![[paper-p5-img0.png]]

Рисунок 2 показывает...
```

В wiki-страницах агент **может включить** те же изображения через `![[image.png]]`
если они существенны для понимания концепции. Изображения из `_attachments/`
автоматически отображаются в Obsidian как embedded previews.
