# URL ingestion

Триггер: пользователь передаёт URL начинающийся с `https://`.

**Зависимость:** `defuddle` (см. `.claude/skills/defuddle/SKILL.md`). Без него URL ingestion не работает. Если `which defuddle` пуст — попросить пользователя установить:

```bash
npm install -g defuddle
```

## Шаги

### 1. Дедуп по URL

Прочитать `raw/meta/ingested.json`. Если есть запись с `source_url == [url]` И файл по её path существует:

- Скачать страницу заново (`defuddle parse [url] --markdown`)
- Посчитать sha256 нового тела
- Совпал с сохранённым `hash` → skip ("страница не изменилась с прошлого ingest")
- Не совпал → перезаписать файл и продолжать ingest

Если запись отсутствует — продолжать как новый источник.

Полные детали manifest — `references/dedup.md`.

### 2. Извлечь slug из URL

Последний сегмент пути, lowercase, пробелы → дефисы, без query string и фрагмента.

Примеры:
- `https://lilianweng.github.io/posts/2018-04-08-policy-gradient/` → slug = `2018-04-08-policy-gradient`
- `https://example.com/articles/My Cool Article?utm=foo` → slug = `my-cool-article`

### 3. Скачать и очистить через defuddle

```bash
URL="https://..."
SLUG="policy-gradient"
DATE=$(date +%Y-%m-%d)
DEST="raw/articles/${SLUG}-${DATE}.md"

mkdir -p raw/articles
BODY=$(defuddle parse "${URL}" --markdown)
{
  echo "---"
  echo "source_url: ${URL}"
  echo "fetched: ${DATE}"
  echo "---"
  echo ""
  echo "${BODY}"
} > "${DEST}"
```

defuddle сохраняет:
- Дословный текст статьи (никакой суммаризации)
- Картинки как `![alt](url)` со ссылками на оригинал
- Формулы LaTeX (`$...$`, `$$...$$`)
- Заголовки, списки, таблицы, code blocks

Удаляет: nav, sidebar, ads, cookie banners, footer, share-кнопки, related articles.

### 4. Hash для дедупа

`sha256` от `${BODY}` (без frontmatter). Сохранить для записи в manifest на шаге 5 (по завершении ingest).

Почему только тело: frontmatter с `fetched: YYYY-MM-DD` меняется каждый запуск. Если хешировать целиком, тот же URL без изменений в контенте даст разные hash в разные дни и дедуп будет ложно срабатывать как "новый".

### 5. Synthesis Workflow

Продолжать с Phase 1 (`references/synthesis-phases.md`) на сохранённом файле.

После Phase 8 запись в `raw/meta/ingested.json`:

```json
"raw/articles/policy-gradient-2026-04-30.md": {
  "source_url": "https://...",
  "hash": "<sha256 от BODY>",
  "ingested_at": "<ISO timestamp>",
  "pages_created": [<wiki paths>],
  "pages_updated": [<wiki paths>]
}
```

## Когда defuddle не справляется

- **Сайт за пейволом / anti-bot.** defuddle получит обрезанный контент или 403. Ручной режим: открыть в браузере → скопировать текст → сохранить руками в `raw/articles/[slug]-[date].md` → `/ingest [файл]`.
- **SPA (React-приложения с client-side rendering).** defuddle не рендерит JavaScript. Аналогично — ручной copy-paste.
- **PDF за URL.** defuddle для HTML, не для PDF. Скачать `curl -o file.pdf [url]` → конвертировать в текст (pdftotext) → сохранить в `raw/`.
