---
name: snapshot
description: >
  Тяжёлый контур vault-дашборда: перегенерация dated UMAP + force-graph
  карт, пересчёт sankey/treemap, генерация LLM-инсайтов для 5 визуализаций
  (UMAP, force-graph, sankey, treemap, word cloud). Wrapper над
  `bin/knowledge_map.py` + ручная запись инсайтов в `heavy.json`. Лёгкие
  метрики дашборда (счётчики, time-series, pulse) обновляются автоматически
  через Stop-hook — этот скилл нужен только когда юзер хочет force-rebuild
  тяжёлой части. По умолчанию запускается раз в сутки (триггерится извне
  или вручную). Триггеры: /snapshot, snapshot, "слепок vault",
  "обнови карту знаний", "force-rebuild дашборда", "сделай снимок".
---

# snapshot: тяжёлый контур дашборда

Лёгкая часть дашборда (числовые метрики, распределения, time-series,
pulse, word cloud) обновляется автоматически после каждого Stop-hook
через `bin/update_dashboard.py`. **Этот скилл руками не нужен**, чтобы
актуализировать цифры.

`/snapshot` нужен для **тяжёлой части**:

- Перегенерация UMAP-карты (10-30 сек, требует numpy + umap)
- Перегенерация force-directed графа топологии
- Пересчёт sankey (домены ↔ сообщества Louvain) и treemap (домены × типы)
- **LLM-инсайты** для 5 визуализаций — это твоя работа как агента,
  Python-скрипт не делает отдельных API-вызовов

Дашборд живёт в `wiki/meta/vault-explorer.html`. После `/snapshot`
обновятся карты в табе «Карты» и инсайт-блоки под ними.

---

## Команда и флаги

| Команда | Поведение |
|---|---|
| `/snapshot` | Полный тяжёлый прогон: карты + sankey + treemap + 5 инсайтов |
| `/snapshot --no-insights` | Только карты и данные, без LLM-инсайтов (инсайты остаются прежними) |
| `/snapshot --no-graph` | Пропустить force-graph рендеринг |
| `/snapshot --no-edges` | UMAP без линий wikilinks |
| `/snapshot --seed N` | Другой UMAP random_state |

Под капотом — `python3 bin/knowledge_map.py [флаги]` + ручной шаг
записи инсайтов + `python3 bin/update_dashboard.py`.

---

## Workflow

### Шаг 1. Запустить knowledge_map.py

```bash
python3 bin/knowledge_map.py [--no-graph] [--no-edges] [--seed N]
```

Скрипт делает:
- UMAP-проекцию эмбеддингов → `_attachments/snapshot-YYYY-MM-DD.html`
- Force-graph (fcose) → `_attachments/snapshot-graph-YYYY-MM-DD.html`
- Sankey + treemap данные → `wiki/meta/snapshots/heavy.json`
- В конце вызывает `bin/update_dashboard.py` чтобы свежий heavy.json
  попал в `vault-explorer.html`

После этого карты в табе «Карты» обновлены, но инсайты остались
прежними (или пустыми, если snapshot ещё не запускался).

### Шаг 2. Прочитать heavy.json + текущие метрики

```bash
cat wiki/meta/snapshots/heavy.json
```

Также прочитай последнюю строку `wiki/meta/snapshots/history.jsonl` —
там свежие числовые метрики vault'а на этот момент.

### Шаг 3. Сгенерировать 5 инсайтов

Это твоя работа. Не делай Python API-вызовов — ты сам и есть LLM.

Для каждой из 5 карт напиши **короткий инсайт** на основе данных:

#### UMAP × домены (`insight-umap`)

Что показывает: семантическая карта (близость = эмбеддинг similarity), цвет = primary domain.

Дано в данных: counts по доменам, domain cohesion (avg internal cosine), список доменов с долями, самая изолированная страница из history.

Что писать:
- Какой домен растянут, какой компактный (из cohesion)
- Кто оторвался от своего кластера (изолированная или low cohesion outliers)
- Кандидаты на re-domain или split

#### Force-graph × сообщества Louvain (`insight-graph`)

Что показывает: топология wikilinks, сообщества Louvain.

Дано: Q-модулярность, число сообществ, топ-мост.

Что писать:
- Совпадают ли сообщества с доменами (Q насколько высокий)
- Кто главный мост (междисциплинарный)
- Аномалии: страница в сообществе чужого домена

#### Sankey: домены ↔ сообщества (`insight-sankey`)

Что показывает: потоки страниц из доменов в Louvain-сообщества.

Дано: `heavy.sankey` — массив `{from, to, flow}`.

Что писать:
- Домены, чьи страницы в основном попадают в одно сообщество (чистые)
- Домены, размазанные по 2-3+ сообществам (междисциплинарные)
- Конкретные числа потоков

#### Treemap: домен → тип (`insight-treemap`)

Что показывает: структурный профиль каждого домена.

Дано: `heavy.treemap` — массив `{domain, type, value}`.

Что писать:
- Какой домен из чего состоит (доля idea / entity / mind / unassigned)
- Аномалии: много unassigned в одном домене (надо классифицировать),
  слишком много / мало entity для области

#### Word cloud (`insight-wordcloud`)

Что показывает: топ-слов в текстах wiki-страниц по частоте.

Дано: `data.wordcloud` в текущем `vault-explorer.html`, или повторно
запусти `bin/update_dashboard.py` — оно пишет wordcloud в HTML. Можно
взять топ-10 слов из swежего HTML (extract из inline JSON).

Что писать:
- Какие термины доминируют → отражает тематический акцент vault
- Шумные мета-слова в топе (кандидаты на stop-list)
- Свежие термины с малой частотой (зарождающиеся темы)

### Формат инсайта

HTML-фрагмент (вставляется внутрь `.map-insight` блока). Шаблон:

```html
<краткое наблюдение в 1-2 предложения>
<ul>
  <li>Конкретный пункт 1 с числом или [[wikilink]]</li>
  <li>Конкретный пункт 2</li>
  <li>Конкретный пункт 3 (опционально)</li>
</ul>
```

- 2-4 буллета на инсайт
- Числа должны браться из данных, не из памяти
- Wikilinks `[[X]]` автоматически превращаются в obsidian:// ссылки
- Без воды, без общих фраз ("Vault выглядит здорово")

### Шаг 4. Записать инсайты в heavy.json

Прочитай `wiki/meta/snapshots/heavy.json`, замени поле `insights`
объектом с 5 ключами:

```json
{
  ...
  "insights": {
    "umap": "<html-фрагмент>",
    "graph": "<html-фрагмент>",
    "sankey": "<html-фрагмент>",
    "treemap": "<html-фрагмент>",
    "wordcloud": "<html-фрагмент>"
  }
}
```

Используй `Edit` или `Write` для записи. Не сломай остальные поля
(`sankey`, `treemap`, `umap_iframe`, `graph_iframe`, `snapshot_date`).

### Шаг 5. Refresh дашборда

```bash
python3 bin/update_dashboard.py --trigger "/snapshot"
```

Этот шаг забирает инсайты из heavy.json и встраивает в финальный
HTML. Без него дашборд продолжит показывать placeholder «будет
сгенерирован при следующем /snapshot».

### Шаг 6. Сообщить пользователю

Короткий summary:

```
🗺️ Слепок vault готов · 2026-05-17

Метрики: 62 страницы, 4 домена, 629 wikilinks, Q=0.401
Карты: 4 раскраски обновлены (UMAP × домены / Force-graph × communities /
       Sankey / Treemap) + word cloud
Инсайты: 5 написано

Открой wiki/meta/vault-explorer.html → таб «Карты».
```

---

## Когда вызывается

- **Пользователь явно**: `/snapshot` или вариации триггера
- **Раз в сутки автоматически** (через cron / scheduled task, опционально)
- **После крупного batch-ingest** (≥5 страниц добавлено) — `/ingest` может
  предложить вызвать `/snapshot` после batch завершения

Лёгкая часть дашборда **не зависит** от `/snapshot` — она обновляется
после каждого Stop-hook'а. `/snapshot` только обновляет карты и инсайты.

---

## Что скилл не делает

- **Не обновляет числовые метрики** — это работа `bin/update_dashboard.py`
  через Stop-hook
- **Не редактирует content-файлы** (`wiki/ideas/`, `wiki/entities/`)
- **Не пишет в `log.md`** — это операция-консьюмер, не операция-производитель
- **Не запускает embedder** — свежесть эмбеддингов поддерживается через Stop-hook
- **Не удаляет старые dated карты** в `_attachments/` — это история, накапливается
