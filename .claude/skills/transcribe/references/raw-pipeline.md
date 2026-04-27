# Raw pipeline: Step 1 + Step 2

Полный технический процесс конвертации бинарного источника в markdown.

---

## Файловая структура

```
raw/formats/              ← оригиналы (PDF, DOCX, audio — иммутабельные)
raw/                      ← восстановленные .md-источники
_attachments/             ← изображения, извлечённые из документов
raw/meta/ingested.json    ← манифест (хеши обоих файлов)
```

**Именование восстановленного файла:** `raw/formats/paper.pdf` → `raw/paper.md`  
Правило: взять basename, убрать расширение, добавить `.md`.

---

## Step 1 — Механическая конвертация

Запускается через скрипт (без LLM):

```bash
python3 bin/transcribe.py raw/formats/paper.pdf
```

Скрипт выводит в stdout:
1. Мета-комментарий для агента (между `<!-- transcribe-meta` и `-->`)
2. Сырой markdown

Пример вывода:
```
<!-- transcribe-meta
source: raw/formats/paper.pdf
format: pdf
pages: 26
large_doc: false
-->

## Fine-Tuning Language Models...

...
```

### PDF (`pymupdf4llm`)

- Библиотека: `pymupdf4llm` (установлена через `bin/setup.sh`)
- Извлекает текст с максимальным сохранением структуры
- Изображения: сохраняются в `_attachments/` с именем `<stem>-p<N>-<idx>.png`
- В markdown-выводе пути вида `_attachments/paper-p5-img0.png`

### DOCX (`pandoc`)

- Утилита: `pandoc` (установлена через `bin/setup.sh`)
- Команда: `pandoc --from=docx --to=gfm --wrap=none --extract-media=_attachments`
- Изображения: сохраняются в `_attachments/` с хеш-именами
- В markdown-выводе пути вида `_attachments/image_001.png`

### Определение размера документа

```bash
python3 bin/transcribe.py --pages raw/formats/paper.pdf
```

Возвращает число страниц. Если > 100 → флаг `large_doc: true` в мета-комментарии.

---

## Step 2 — Агентское восстановление

Агент читает сырой markdown из памяти (stdout скрипта) и восстанавливает структуру.

**Делать:**
- Фиксить артефакты конвертации: сломанные переносы mid-word, лишние пробелы
- Восстанавливать правильные уровни заголовков (H1/H2/H3)
- Приводить в порядок таблицы (выровнять колонки, убрать артефакты)
- Конвертировать пути к изображениям: `_attachments/img.png` → `![[img.png]]`
- Сохранять LaTeX-формулы как есть
- Сохранять wikilinks если они были в документе

**Не делать:**
- Не добавлять контент которого не было в оригинале
- Не переформулировать, не сокращать, не расширять
- Не переструктурировать разделы (только исправлять уровни заголовков)
- Не удалять секции даже если они кажутся лишними

### Пороги по объёму

| Объём | Действие |
|---|---|
| ≤100 страниц | Step 1 + Step 2 (полный pipeline) |
| >100 страниц | Только Step 1. Сохранить сырой вывод напрямую. В frontmatter отметить `restored: false`. |

---

## Сохранение результата

После Step 2 агент записывает финальный markdown с frontmatter:

```yaml
---
source_type: pdf
original_file: raw/formats/paper.pdf
restored: true         # false если large_doc, Step 2 пропущен
restored_at: YYYY-MM-DDTHH:MM:SS
pages: 26
---
```

Файл: `raw/<stem>.md` (e.g. `raw/paper.md`).

---

## Обновление manifesta

В `raw/meta/ingested.json` добавляется запись с **двумя хешами**:

```json
"raw/formats/paper.pdf": {
  "original_hash": "<sha256 бинарного файла>",
  "restored_to": "raw/paper.md",
  "restored_hash": "<sha256 восстановленного .md>",
  "source_type": "pdf",
  "pages": 26,
  "restored": true,
  "transcribed_at": "2026-05-01T..."
}
```

При повторном вызове:
- hash оригинала совпадает → skip (файл не изменился)
- hash изменился → перепрогнать pipeline

---

## Поддерживаемые форматы

| Расширение | Библиотека | Изображения | Аудио/видео |
|---|---|---|---|
| `.pdf` | pymupdf4llm | ✓ | — |
| `.docx` | pandoc | ✓ | — |
| `.mp3/.wav/.m4a` | whisper-cpp | — | ✓ (future) |
| `.mp4/.mov` | ffmpeg + whisper | — | ✓ (future) |
