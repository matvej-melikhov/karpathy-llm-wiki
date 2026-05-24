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
bash bin/init.sh         # интерактивный wizard: зависимости, .env, Obsidian, git
claude                   # запустить агента
```

Скрипт `init.sh` задаст вопросы по каждому опциональному компоненту (pandoc, whisper, defuddle, embedding-провайдер) и покажет все команды перед выполнением — никакой магии. В конце переинициализирует git под твой remote, чтобы ты сразу мог push'ить свою базу знаний.

> **ANTHROPIC_API_KEY** не идёт в `.env` — им управляет Claude Code напрямую.  
> Установи через `export ANTHROPIC_API_KEY=...` или `claude config`.

### Что настраивает `init.sh`

| Шаг | Что делает |
| --- | --- |
| 1. Python deps | `pip install` обязательных зависимостей (pymupdf4llm, umap, networkx, tiktoken) |
| 2. Системные деп. | pandoc / whisper-cpp / ffmpeg / defuddle — каждый Y/n |
| 3. Embedding | Ollama (локально) / OpenAI-compatible / OpenRouter / пропустить — записывает `.env` |
| 4. Whisper | Опциональная загрузка `ggml-base.bin` для `/transcribe` |
| 5. Obsidian | Создаёт `.obsidian/` с настройками graph view и удаляет `.gitkeep` |
| 6. Git | Переинициализирует репо под твой remote (стирает чужую историю шаблона) |

### Первый запуск

```bash
# Положи источник
cat > raw/test.md <<'EOF'
# RLHF

Reinforcement Learning from Human Feedback — обучение модели по предпочтениям человека.
EOF

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
