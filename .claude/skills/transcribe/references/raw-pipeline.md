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

## Step 1а — Извлечение изображений (скрипт, без LLM)

```bash
python3 bin/transcribe.py raw/formats/paper.pdf
```

Скрипт выводит в stdout JSON-манифест извлечённых изображений:

```json
{
  "source": "raw/formats/paper.pdf",
  "format": "pdf",
  "pages": 26,
  "large_doc": false,
  "images": [
    "_attachments/paper-p0-img0.png",
    "_attachments/paper-page2.png"
  ]
}
```

Алгоритм извлечения по типу PDF:

**Растровые PDF (сканы, фотографии):**
- `pymupdf` находит bounding boxes крупных raster-объектов (≥50pt)
- Соседние регионы объединяются (порог 30pt) — устраняет фрагментацию диаграмм
- Каждый объединённый регион рендерится при 200 DPI → `<stem>-p<N>-img<M>.png`

**Векторные PDF (статьи, typeset документы):**
- Диаграммы нарисованы PDF path операторами — raster bbox не найти
- Страница с изображениями рендерится целиком → `<stem>-pageN.png`

### DOCX (`pandoc`)

- `pandoc --extract-media=_attachments` извлекает медиафайлы
- Изображения сохраняются в `_attachments/`

### Число страниц

```bash
python3 bin/transcribe.py --pages raw/formats/paper.pdf
```

Возвращает число страниц. Если > 100 → `large_doc: true` в манифесте.

---

## Step 1б — Чтение контента (Read tool, нативный multimodal)

Claude читает исходный PDF через **Read tool** — это критически важно:

- **Text PDF**: Claude видит точный текст, формулы (LaTeX), таблицы, структуру
- **Scanned PDF**: Claude видит rendered изображение страницы — читает OCR-качественно
- **Нет потерь формул** — в отличие от pymupdf4llm, Claude корректно распознаёт $\LaTeX$

```
Read file_path="raw/formats/paper.pdf"
```

---

## Step 2 — Агент пишет финальный markdown

Агент имеет:
- JSON-манифест с путями к изображениям
- Полный контент документа из Read tool

**Делать:**
- Писать текст точно по оригиналу — без переформулировок
- Формулы → LaTeX: `$A = \frac{1}{2}bh$` inline, `$$...$$` block
- Таблицы → markdown tables с правильными колонками
- Встраивать изображения из манифеста: `![[img.png]]` в нужных позициях
  - `pageN.png` — ставить там, где в тексте Figure N / Рис. N
  - Отдельные фигуры — рядом с их подписью
- Заголовки, списки, жирный/курсив — точно по оригиналу
- Ссылки и цитаты (Author et al., YYYY) — сохранять

**Не делать:**
- Не добавлять пояснений, резюме, комментариев
- Не переформулировать
- Не удалять секции

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

## Дедуп при повторном вызове

Transcribe не пишет в `raw/meta/ingested.json` — это зона ingest.

Проверка при повторном `/transcribe`:
- Существует `raw/<stem>.md` И он **новее** оригинала (`raw/formats/<file>`) → skip
- Оригинал обновился или `.md` не существует → запустить pipeline
- `--force` → игнорировать проверку

---

## Поддерживаемые форматы

| Расширение | Библиотека | Изображения | Аудио/видео |
|---|---|---|---|
| `.pdf` | pymupdf | ✓ | — |
| `.docx` | pandoc | ✓ | — |
| `.mp3/.wav/.m4a/.ogg/.flac` | ffmpeg → whisper-cpp | — | ✓ |
| `.mp4/.mov/.mkv/.webm` | ffmpeg → whisper-cpp | — | ✓ |

---

## Аудио и видео

Для звуковых форматов pipeline отличается:

```
raw/formats/lecture.mp3
         ↓
bin/transcribe.py raw/formats/lecture.mp3
  ├─ ffmpeg: → 16 kHz mono WAV (временный файл)
  ├─ whisper-cpp: WAV → текст
  └─ → _attachments/lecture.transcript.txt
         ↓
Манифест:
{
  "source": "raw/formats/lecture.mp3",
  "format": "mp3",
  "transcript": "_attachments/lecture.transcript.txt",
  "images": []
}
         ↓
Step 2: агент читает транскрипт через Read tool,
структурирует на разделы, чистит от слов-паразитов,
расставляет абзацы. Сохраняет в raw/lecture.md.
```

**Зависимости:** `whisper-cpp` и `ffmpeg` (через `brew install`). Модель указывается через `$WHISPER_MODEL` (путь к `ggml-*.bin`).

**Видео:** ffmpeg извлекает аудиодорожку — дальше идентично аудио-pipeline.
