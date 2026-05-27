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

Один раз залогинься в провайдер (ключ сохранится в `~/.local/share/opencode/auth.json`):

```bash
opencode providers login openrouter
```

Дальше — просто `opencode` в корне репо. Модель и конфиг подхватываются из `opencode.json`.

Сменить модель: правка `model` в `opencode.json` или флаг `opencode -m openrouter/<provider>/<model>`. Список — `opencode models openrouter`.

Для `bin/embed.py` (эмбеддинги, отдельный пайплайн) ключ читается из `.env` в корне (переменные `EMBED_API_KEY`, `EMBED_HOST`, `EMBED_MODEL`).

## Статус

В активной разработке.

## Лицензия

MIT
