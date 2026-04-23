---
name: defuddle
description: >
  Очистить веб-страницу от мусора (nav, ads, sidebar, footer) и вернуть
  дословный текст статьи как чистый markdown. Сохраняет картинки (как
  URL-ссылки) и формулы LaTeX. Используется в URL ingestion как
  единственный инструмент очистки.
  Триггеры: defuddle, clean this page, strip this url, fetch and clean,
  очисти страницу, выгрузи статью, читаемый markdown из URL.
allowed-tools: Bash
---

# defuddle: очистка веб-страниц

`defuddle` — CLI-утилита (от kepano, автор Obsidian Minimal theme и Web Clipper). На базе Mozilla Readability + кастомные чистильщики.

**Что делает:**
- Принимает URL или локальный HTML
- Возвращает дословный текст статьи в markdown
- Сохраняет: текст, заголовки, списки, таблицы, code blocks, **формулы LaTeX** (`$...$`, `$$...$$`), **ссылки на картинки** (`![alt](url)`)
- Удаляет: nav, sidebar, ads, cookie banners, footer, share-кнопки, related articles, скрипты, стили

**Что НЕ делает:** не суммаризирует, не перефразирует, не интерпретирует. Чистый strip + конвертация HTML → markdown.

---

## Установка (зависимость проекта)

```bash
npm install -g defuddle
```

Проверка: `defuddle --version` (требуется `0.18.x` или новее).

defuddle входит в обязательные зависимости проекта — без него URL ingestion не работает. Зависимость от Node.js / npm.

---

## Использование

### Очистить URL и получить markdown

```bash
defuddle parse https://example.com/article --markdown
```

Выводит markdown в stdout.

### Сохранить в `raw/articles/` с frontmatter

Полный flow для URL ingestion:

```bash
URL="https://example.com/article"
SLUG="article-slug"
DATE=$(date +%Y-%m-%d)
DEST="raw/articles/${SLUG}-${DATE}.md"

mkdir -p raw/articles
{
  echo "---"
  echo "source_url: ${URL}"
  echo "fetched: ${DATE}"
  echo "---"
  echo ""
  defuddle parse "${URL}" --markdown
} > "${DEST}"
```

После сохранения файл готов для synthesis workflow.

### Очистить локальный HTML

```bash
defuddle parse page.html --markdown
```

### Извлечь только метаданные

```bash
defuddle parse "${URL}" --json
```

Выводит JSON с полями: title, description, author, published, content. Полезно для авто-заполнения frontmatter.

---

## Когда использовать

**Да:**
- Любой URL ingestion (статья, блог-пост, документация)
- Длинные тексты с обилием обвеса (Medium, Substack, news-сайты)
- Когда хочется дословный текст автора, а не суммаризацию

**Нет:**
- Источник уже чистый markdown или PDF
- Страница — dashboard / SPA / структурированные данные (defuddle ожидает article-style)
- Сайт за anti-bot защитой (defuddle не обходит CAPTCHA / JS-challenges)

---

## Интеграция с wiki-ingest

Скилл `wiki-ingest` вызывает defuddle автоматически при URL ingestion. См. секцию "URL ingestion" в `.claude/skills/wiki-ingest/SKILL.md`.

Вручную скачать страницу и затем ingestить:

```bash
defuddle parse "${URL}" --markdown > "raw/articles/${SLUG}-$(date +%Y-%m-%d).md"
# затем:
# /ingest raw/articles/${SLUG}-${DATE}.md
```
