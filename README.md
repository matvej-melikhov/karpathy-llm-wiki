# llm-wiki

Реализация паттерна LLM Wiki от Андрея Карпаты в формате [OpenCode](https://opencode.ai/) проекта и Obsidian vault.

## Идея

Вместо того чтобы каждый раз заново читать сырые документы (классический RAG), LLM строит и поддерживает структурированную базу знаний — wiki из markdown-страниц с перекрёстными ссылками. С каждым новым источником wiki становится богаче.

При запросе LLM не пересинтезирует знание из chunks — он читает уже готовые страницы, где синтез был выполнен один раз при ingestion.

## Источник паттерна

[Andrej Karpathy — LLM Wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)

## Зависимости

Для URL-ingestion требуется [defuddle](https://github.com/kepano/defuddle) — очищает веб-страницы от мусора и возвращает дословный markdown:

```bash
npm install -g defuddle
```

Без него `/ingest <url>` не работает (для file-ingestion из `raw/` defuddle не нужен).

## Запуск

В корне проекта установить плагин-зависимости и запустить OpenCode:

```bash
cd .opencode && bun install && cd ..
opencode
```

API-ключ к OpenRouter — в переменной окружения `OPENROUTER_API_KEY` (модель и провайдер настроены в `opencode.json`).

## Статус

В активной разработке.

## Лицензия

MIT
