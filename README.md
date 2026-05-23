<p align="center">
  <img src="./assets/cover.svg" alt="llm-wiki — a persistent, agent-managed knowledge base" width="100%"/>
</p>

# llm-wiki

Реализация паттерна **LLM Wiki** ([Andrej Karpathy](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)) поверх Obsidian-vault, управляемая через Claude Code.

> Вместо того чтобы каждый раз заново читать сырые документы (классический RAG), LLM строит и поддерживает структурированную базу знаний — wiki из markdown-страниц с перекрёстными ссылками. С каждым источником wiki становится богаче. При запросе агент не пересинтезирует знание из чанков — он читает уже готовые страницы, где синтез был выполнен один раз при ingestion.

---

## Что внутри

| Слой | Что хранится | Кто пишет |
| --- | --- | --- |
| `raw/` | Источники: md, pdf, docx, видео-транскрипты, URL-снимки. Один файл — один источник. Иммутабельно. | пользователь, `/transcribe` |
| `wiki/ideas/` | Концепции, механизмы, теории (RLHF, YetiRank, Decision Tree, …) | `/ingest`, `/study`, `/save` |
| `wiki/entities/` | Люди, организации, статьи, библиотеки, модели | `/ingest` |
| `wiki/domains/` | Навигационные хабы по областям (MOC), создаются при пороге N=10 | `/ingest` |
| `wiki/questions/` | Сохранённые ответы из чата | `/save`, `/query` |
| `wiki/minds/` | Авторские мысли, склеенные из brainstorm-сессий | `/brainstorm` |
| `wiki/meta/` | Эмбеддинги, lint-state, dated snapshots, `vault-explorer.html` (живой дашборд), `history.jsonl`, `heavy.json` — derivable | `bin/*`, `/lint`, `/snapshot`, `update_dashboard.py` |
| `wiki/{cache,log,index,summary}.md` | Горячий контекст, журнал, каталог, обзор | скиллы + `bin/gen_index.py` |

Per-user контент (`raw/`, `wiki/`, `_attachments/`) исключён из репозитория. Коммитится только инфраструктура: skills, шаблоны, скрипты.

Полный design-doc — [`ARCHITECTURE.md`](./ARCHITECTURE.md). Схема страниц и frontmatter — [`.claude/CLAUDE.md`](./.claude/CLAUDE.md).

---

## Скиллы

Каждый скилл — один контракт зоны ответственности; запускается через slash-команду.

| Скилл | Команда | Что делает |
| --- | --- | --- |
| **ingest** | `/ingest` | Читает источник из `raw/` или URL, синтезирует страницы `ideas/entities`, ставит wikilinks, обновляет cache/log |
| **query** | `/query` | Отвечает на вопрос из vault: cache → index → relevant pages. Цитирует источники |
| **save** | `/save` | Сохраняет ответ или инсайт из чата как wiki-страницу с frontmatter и wikilinks |
| **brainstorm** | `/brainstorm` | Модерирует мозговой штурм по seed; склеивает permanent note (`mind`) дословно из реплик пользователя |
| **study** | `/study` | Учебный режим: отвечает по training knowledge + WebSearch, по запросу файлирует в wiki |
| **edge** | `/edge` | Показывает фронтир базы — страницы с большим out-/in-link disbalance, предлагает что углубить дальше |
| **lint** | `/lint` | Статические + LLM-проверки wiki, автофиксы, диалог по ask-issues |
| **snapshot** | `/snapshot` | Тяжёлый контур vault-дашборда: UMAP + force-graph + sankey + treemap + 5 LLM-инсайтов. Дашборд (`wiki/meta/vault-explorer.html`) сам по себе обновляется числовыми метриками после каждого turn'а через Stop-hook → `bin/update_dashboard.py` |
| **transcribe** | `/transcribe` | PDF/DOCX → markdown в `raw/` (mechanical convert + agentic structure repair) |
| **defuddle** | внутренний | Чистит web-страницы от nav/ads/sidebar, отдаёт markdown для url-ingest |
| **obsidian-bases** | внутренний | Создание Obsidian Bases-файлов (.base) для динамических view |
| **obsidian-markdown** | внутренний | Гайд по Obsidian-flavored markdown: wikilinks, embeds, properties |
| **help** | `/help` | Список всех скиллов или подробная справка по одному (`/help <команда>`) |

Скиллы лежат в [`.claude/skills/`](./.claude/skills); каждый — самостоятельный SKILL.md с инструкцией и ссылками на references.

---

## Quick start

Требуется [Claude Code](https://docs.anthropic.com/claude/docs/claude-code) и Python 3.11+.

```bash
git clone <repo> && cd llm-wiki

bash bin/setup.sh        # python-зависимости + defuddle (Node) + pandoc + whisper-cpp
bash bin/setup-vault.sh  # Obsidian-конфиг (.obsidian/) + удаление .gitkeep-заглушек

cp .env.example .env     # embedding + транскрипция (опционально, см. ниже)

claude                   # запустить агента в этой директории
```

> **ANTHROPIC_API_KEY** не идёт в `.env` — им управляет Claude Code напрямую.  
> Установи через `export ANTHROPIC_API_KEY=...` в shell или через `claude config`.

### Embedding (опционально)

Нужен только для `/lint --approx`. Без него всё остальное работает полностью. Выбери один провайдер и впиши значения в `.env`.

**Вариант A — Ollama (локально, бесплатно, рекомендуется):**

```bash
brew install ollama && ollama serve &
ollama pull nomic-embed-text
```

```env
EMBED_PROVIDER=ollama
EMBED_MODEL=nomic-embed-text
EMBED_HOST=http://localhost:11434
```

**Вариант Б — LMStudio или другой OpenAI-compatible сервер:**

```env
EMBED_PROVIDER=openai
EMBED_HOST=http://localhost:1234/v1
EMBED_MODEL=<имя-модели>
EMBED_API_KEY=
```

**Вариант В — OpenRouter (облако, платно):**

```env
EMBED_PROVIDER=openai
EMBED_HOST=https://openrouter.ai/api/v1
EMBED_MODEL=qwen/qwen3-embedding-8b
EMBED_API_KEY=<openrouter-key>
```

Проверить подключение: `python3 bin/embed.py update`

### Транскрипция (опционально)

Нужна только для `/transcribe <audio-или-video>`. Скачай whisper-модель ([список](https://github.com/ggerganov/whisper.cpp#models)) и пропиши путь:

```env
WHISPER_MODEL=~/models/ggml-base.bin
```

### Первый запуск

```bash
# Положи источник
echo "# RLHF\nReinforcement Learning from Human Feedback." > raw/test.md

claude
```

В сессии:

```
/ingest raw/test.md     — синтезировать источник в wiki-страницы
/query что такое RLHF?  — спросить по базе
/edge                   — посмотреть фронтир (что стоит углубить)
/lint                   — проверить связность wiki
/snapshot               — собрать дашборд (UMAP, граф, sankey)
/help                   — справка по любому скиллу
```

Wiki совместима с Obsidian — открой папку как vault (`Manage Vaults → Open folder as vault`).

---

## Структура репозитория

```
.claude/         # Claude Code: skills, agents, commands, settings, hooks
.opencode/       # placeholder для альтернативной opencode CLI конфигурации
_templates/      # frontmatter templates: idea / entity / domain / question / mind / meta
assets/          # ассеты README (cover image)
bin/             # генераторы (embed, gen_index, knowledge_map, lint, transcribe…)
raw/             # per-user источники (gitignored)
wiki/            # per-user синтез (gitignored)
ARCHITECTURE.md  # design-doc: контракты слоёв, скиллов, скриптов
README.md
```

`bin/embed.py`, `bin/gen_index.py`, `bin/gen_dashboards.py` запускаются автоматически Stop-hook'ом после каждого turn'а — скиллы их не вызывают.

---

## Ветки

- **`main`** — реализация под Claude Code (этот README).
- **`opencode`** — альтернативная конфигурация под [opencode](https://opencode.ai/) CLI. Статус — early.
- **`benchmarking`** — скрипты и результаты замеров производительности (§4.4–4.5 ВКР).
- **`tests`** — unit-тесты инфраструктурных скриптов (`bin/tests/`).

---

## Контекст

Часть ВКР по гибридному фреймворку организации знаний (иерархия + Zettelkasten + Mind Mapping). Реализация ножек:

- **иерархия** — `wiki/domains/` как MOC (map of content);
- **Zettelkasten** — `wiki/ideas/` + `wiki/entities/` + плотные wikilinks;
- **Mind Mapping** — `wiki/minds/` через `/brainstorm`.

## Лицензия

MIT
