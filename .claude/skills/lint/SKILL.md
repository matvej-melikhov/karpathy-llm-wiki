---
name: lint
description: >
  Read-only ревьюер Obsidian wiki-vault. Находит страницы-сироты, мёртвые wikilinks,
  устаревшие утверждения, отсутствующие перекрёстные ссылки, пробелы во frontmatter,
  пустые секции, нарушения схемы. Записывает результат в `wiki/meta/lint-reports/lint-state.json`,
  включая структурированный список `open_issues` для применения ingest. Кэширует
  состояние через aggregate hash, чтобы не перепроверять неизменную wiki.
  Триггеры: /lint, "lint", "проверка здоровья", "очисти wiki",
  "проверь wiki", "найди сирот", "wiki audit".
---

# lint: ревьюер wiki

Lint **только читает** wiki и записывает структурированный отчёт. **Никаких правок в content-файлы (`wiki/ideas/`, `wiki/entities/` и т.д.)** — фиксы делает `ingest` по этому отчёту.

Единственный файл, который lint имеет право писать — `wiki/meta/lint-reports/lint-state.json`.

---

## Two-layer (плюс опциональный 1.5) архитектура

Lint работает в два слоя плюс опциональный embedding-based слой:

**Layer 1 — программная проверка (`bin/lint.py`).** Python-скрипт, реализующий все детерминистические проверки: 16 типов issues. Запускается за секунды, без LLM-стоимости. Пишет `lint-state.json` с найденными `open_issues`.

**Layer 1.5 — embedding-based (опционально, `--approx`).** Те же `bin/lint.py`, но с флагом `--approx` подключаются проверки на основе предварительно посчитанных эмбеддингов: `similar-but-unlinked` (semantic missing links) и `synthesis-drift` (детектор отклонения синтеза от источников). Чистые потребители векторов — embedding-сервер (Ollama или LMStudio через OpenAI-совместимый API) не нужен для lint, только `bin/embed.py update` должен быть выполнен заранее.

Кроме issues, при `--approx` Layer 1.5 пишет в `lint-state.json` отдельное поле `contradiction_candidates` — список пар страниц с высоким cosine, для которых **Layer 2** должен запускать LLM-проверку на противоречия. Это сужает работу Layer 2 с O(n²) до top X% пар (~5-6× редукция при дефолтном `--candidate-percentile 75`).

**Layer 2 — LLM-семантическая проверка.** Этот скилл (lint) запускается после `bin/lint.py`, читает уже записанный state, дополняет `open_issues` семантическими проверками: `contradiction`, `outdated-claim`, `missing-concept`, `style-nit`. Это требует языкового суждения, программно не делается.

Полный поток `/lint`:

```
1. python3 bin/lint.py [--approx]  # Layer 1 (+ 1.5 если --approx)
2. lint skill (этот документ)       # Layer 2: LLM добавляет семантические issues
3. Итог: lint-state.json содержит все категории open_issues
```

Если Layer 1 упал (нет Python и т.д.), скилл может выполнить детерминистические проверки сам, но это медленнее и дороже токенов.

---

## Команда и флаги

| Команда | Поведение |
|---|---|
| `/lint` | Запустить Layer 1 + Layer 2. Skip-check по hash. Если wiki не менялась И нет open_issues — пропуск. |
| `/lint --force` | Игнорировать skip-check, всегда full audit (оба слоя). |
| `/lint --fast` | Только Layer 1 (программный), без LLM-фазы. Быстро, но без семантических проверок. |
| `/lint --approx` | Layer 1 + Layer 1.5 + Layer 2. Подключает embedding-based проверки. Эмбеддинги поддерживает Stop-hook; если их нет (Ollama / модель недоступны) — слой просто пропустится с предупреждением. |
| `/lint --approx --fast` | Layer 1 + Layer 1.5 без LLM-слоя. Покрывает максимум структурных + семантических нарушений без затрат на LLM. |

**Параметры тонкой настройки `--approx`:**

| Флаг | По умолчанию | Что делает |
|---|---|---|
| `--similarity-percentile FLOAT` | 95 | Для `similar-but-unlinked`: пары выше этого перцентиля попарных косинусов считаются близкими |
| `--drift-std FLOAT` | 1.5 | Для `synthesis-drift`: множитель std-deviations над средним drift'ом |

Плюс встроенные floor-значения (cosine ≥ 0.6, drift ≥ 0.1) — защита от ложных срабатываний на «плоских» распределениях.

После audit:
- если есть open_issues — список выводится пользователю
- предлагается: "Применить через `/ingest --fix`?"

---

## Skip-check (агрегатный hash)

Skip-check выполняется в Layer 1 (`bin/lint.py`). Если wiki не менялась с последнего audit и нет накопленных open_issues — оба слоя пропускаются.

`wiki/meta/lint-reports/lint-state.json` хранит результат последнего audit:

```json
{
  "wiki_hash": "<sha256 от конкатенации всех wiki/**/*.md>",
  "last_audit": "2026-04-29T15:45:00",
  "files_checked": 11,
  "open_issues": [
    {
      "type": "dead-link",
      "where": "wiki/ideas/RLHF.md",
      "what": "[[SFT]]",
      "context": "упомянута на строке 32"
    }
  ]
}
```

**При входе в `/lint`:**

1. Если `wiki/meta/lint-reports/lint-state.json` отсутствует — пропустить skip-check, идти на full audit.
2. Прочитать `lint-state.json`, получить `wiki_hash` и `open_issues`.
3. Посчитать текущий `wiki_hash`:
   ```bash
   find wiki -name '*.md' \
     -not -path 'wiki/meta/lint-reports/*' \
     -not -path 'wiki/meta/kn-maps/*' \
     -not -path 'wiki/meta/dashboards/*' \
     | sort | xargs cat | sha256sum | cut -d' ' -f1
   ```
   (Сортировка чтобы порядок не влиял; конкатенация всех тел; один итоговый sha256.)
4. Сравнить с сохранённым `wiki_hash`:
   - Совпадает И `open_issues` пуст → пропуск:
     ```
     Wiki не менялась с последнего audit (2026-04-29 15:45). Чисто. Пропускаю.
     ```
   - Совпадает И `open_issues` не пуст → не аудитим заново, **показываем сохранённые `open_issues`** + предлагаем починить через `/ingest --fix`.
   - Не совпадает → full audit.

С `--force` шаги 1–4 пропускаются.

---

## Full audit

Полный обход wiki, без правок. По завершении:

1. Записать `wiki/meta/lint-reports/lint-state.json` с актуальным `wiki_hash`, `last_audit`, `files_checked`, новым списком `open_issues`.
2. Вывести пользователю краткую сводку + список issues.
3. Предложить: "Применить безопасные правки и пройтись по требующим решения через `/ingest --fix`?"

---

## Layer 2: contradiction-check (LLM)

Layer 2 запускается **этим скиллом** после `bin/lint.py` отработал. Задача — найти семантические нарушения, которые программно не поймать: `contradiction`, `outdated-claim`, `missing-concept`.

### Contradiction check — стратегия зависит от наличия `contradiction_candidates`

**Если в `lint-state.json` есть поле `contradiction_candidates`** (запустили с `--approx`):

```json
{
  "contradiction_candidates": [
    {"page_a": "wiki/ideas/A.md", "page_b": "wiki/ideas/B.md", "similarity": 0.87},
    {"page_a": "...", "page_b": "...", "similarity": 0.84},
    ...
  ]
}
```

Это уже отфильтрованный embedding-pre-filter список пар, которые семантически близки и потенциально могут содержать противоречия. Проходим **только по этим парам**, не по всему O(n²).

Поведение:
1. Прочитать список candidates (отсортирован по similarity descending — самые похожие сверху)
2. Для каждой пары прочитать обе страницы, сравнить ключевые утверждения
3. Если найдено противоречие — добавить в `open_issues`:
   ```json
   {"type": "contradiction", "page_a": "...", "page_b": "...", "claim": "<краткое описание противоречия>"}
   ```
4. Если противоречий нет — ничего не делать (не флагуем «проверено и чисто»)

**Если поля `contradiction_candidates` нет** (запустили без `--approx`):

Классический полный обход. Перебрать все content-страницы попарно (O(n²)) и проверить на противоречия. Дорого по токенам — для wiki >50 страниц рекомендуем включать `--approx` чтобы пользоваться pre-filter.

### Outdated-claim и missing-concept

Эти проверки идут **по всему content** wiki независимо от `--approx`:
- `outdated-claim` — найти утверждения в страницах, опровергнутые более новыми источниками
- `missing-concept` — концепции, которые упоминаются в ≥3 страницах, но не имеют своей wiki-страницы

### Domain-order (порядок доменов)

Convention wiki: в поле `domain:` домены расположены **от частного к общему** — первый в списке считается primary classification (используется в knowledge map для раскраски). Например, для PPO правильный порядок:

```yaml
domain:
  - "[[Reinforcement Learning]]"   # узкий
  - "[[Machine Learning]]"          # широкий
```

**Почему это LLM-проверка, а не скриптовая.** Иерархия доменов («RL — поддомен ML») — это знание о мире, не структурное свойство wiki. Скриптовые подсчёты («больше страниц = шире») ломаются при появлении нового широкого домена (он начнёт с count=1) или при наличии cross-cutting узкого. Агент же использует общее знание: видит «Reinforcement Learning» и «Machine Learning» — понимает, что первое ⊂ второго.

Поведение агента:
1. Для каждой страницы с ≥2 доменами в frontmatter
2. Применить семантическое суждение о их относительной широте/узости
3. Если порядок не соответствует «от частного к общему» — добавить issue:
   ```json
   {
     "type": "domain-order",
     "where": "wiki/ideas/PPO.md",
     "current": ["Machine Learning", "Reinforcement Learning"],
     "expected": ["Reinforcement Learning", "Machine Learning"],
     "reasoning": "RL — поддомен ML"
   }
   ```
4. Если домены разнородные и иерархии нет (например, `[Machine Learning, Knowledge Management]` — параллельные) — пропустить без issue

Категория: **auto-fix** — ingest применяет переупорядочивание молча, без подтверждения. Reasoning заносится в issue для возможного аудита. Если агент не уверен в иерархии (например, для параллельных доменов вроде ML+KM) — issue не создаётся вовсе, страница не трогается.

---

## Категории issues

Каждый issue в `open_issues` имеет поле `type` — категория. По типу `ingest` решает, как с ним поступить.

### Auto-fix (ingest правит молча)

Детерминистические нарушения схемы, единственный очевидный fix:

| `type` | Условие | Структура issue |
|---|---|---|
| `status-not-in-enum` | `status` не из `evaluation/in-progress/ready` | `{type, where, value, fix: "in-progress"}` |
| `status-on-entity` | у `type: entity` присутствует поле `status` | `{type, where}` |
| `legacy-field` | поля старой схемы (`title`, `complexity`, `first_mentioned`) на не-meta | `{type, where, field}` |
| `lowercase-tags` | теги в lowercase или со смешанным регистром аббревиатур | `{type, where, tags: [...]}` |
| `inline-tags` | `tags: [a, b]` инлайн вместо блочного YAML | `{type, where}` |
| `raw-link-with-extension` | `[[raw/X.md]]` вместо `[[raw/X]]` | `{type, where, link}` |
| `raw-ref-in-body` | упоминание `[[raw/...]]` в теле страницы | `{type, where, link, line}` |
| `empty-sources-section` | секция `## Источники`/`## Источники упоминания` содержит только `[[raw/...]]` | `{type, where, section}` |
| `folder-type-mismatch` | страница лежит в `wiki/<X>/`, но `type:` во frontmatter не соответствует папке | `{type, where, current_type, expected_type}` |
| `stale-index-entry` | строка в `wiki/index.md` ссылается на несуществующую страницу (была удалена/переименована) | `{type, link, section}` |
| `non-canonical-wikilink` | wikilink использует path-prefixed форму (`[[wiki/ideas/RLHF]]`, `[[ideas/RLHF]]`) вместо канонической basename `[[RLHF]]`. raw/-ссылки не флагируются. Auto-fix сохраняет `#section\|alias` части | `{type, where, link, fix, context}` |
| `domain-order` | LLM-проверка (Layer 2): `domain:` не упорядочен от частного к общему. Агент использует семантическое знание о соотношении доменов (`RL ⊂ ML`). Auto-fix переписывает блок в порядке из `expected`. Параллельные домены (без иерархии) агент пропускает | `{type, where, current, expected, reasoning}` |

### Ask user (ingest спрашивает решение)

Требует суждения:

| `type` | Условие | Структура issue |
|---|---|---|
| `dead-link` | wikilink на несуществующую страницу | `{type, where, what, context}` |
| `orphan` | страница без входящих wikilinks | `{type, where}` |
| `missing-concept` | концепция упомянута в ≥3 страницах без своей wiki-страницы | `{type, term, mentioned_in: [...]}` |
| `contradiction` | противоречие между утверждениями двух страниц | `{type, page_a, page_b, claim}` |
| `outdated-claim` | утверждение в `[[A]]` потенциально опровергнуто `[[B]]` | `{type, where, claim, conflicts_with}` |
| `missing-index-entry` | content-страница (idea/entity/question/domain) существует в файлах, но строка о ней отсутствует в `wiki/index.md` | `{type, where, page_type}` |
| `dangling-domain-ref` | страница имеет в `domain:` frontmatter ссылку на несуществующую domain-страницу | `{type, where, missing_domain}` |
| `asymmetric-related` | у страницы `[[A]]` в `related:` есть `[[B]]`, но у `[[B]]` в `related:` нет `[[A]]` | `{type, page_a, page_b}` |
| `binary-source-outside-formats` | бинарный файл (.pdf/.docx/audio) лежит в `raw/` вне папки `raw/formats/` | `{type, where, suggested}` |
| `similar-but-unlinked` | две страницы семантически близки (cosine выше порога), но wikilink между ними отсутствует. Только в режиме `--approx` | `{type, page_a, page_b, similarity, threshold}` |
| `synthesis-drift` | wiki-страница семантически далеко ушла от центроида эмбеддингов своих источников. Сигнал о возможной галлюцинации синтеза. Только в режиме `--approx` | `{type, where, drift, threshold}` |

### Skip (только записываем, не спрашиваем)

Информационные флаги — пользователю не задаём вопрос:

| `type` | Условие |
|---|---|
| `empty-section` | секция без контента (может быть намеренно) |
| `style-nit` | не декларативное настоящее, отсутствие линка на не-ключевую сущность |

---

## Проверки

| # | Проверка | Категория |
|---|---|---|
| 1 | Сирота (нет входящих wikilinks) | ask |
| 2 | Мёртвая ссылка | ask |
| 3 | Устаревшее утверждение | ask |
| 4 | Пропущенная концепция (≥3 упоминания) | ask |
| 5 | Противоречие | ask |
| 6 | `status` вне enum | auto-fix |
| 7 | `status` на entity | auto-fix |
| 8 | Поля старой схемы (`title`/`complexity`/`first_mentioned`) | auto-fix |
| 9 | Tags casing (lowercase аббревиатур) | auto-fix |
| 10 | Tags inline-формат | auto-fix |
| 11 | `[[raw/X.md]]` (с расширением) в `sources` | auto-fix |
| 12 | Raw-refs в теле страницы | auto-fix |
| 13 | "Sources"-секция с одним raw | auto-fix |
| 13.1 | Папка vs `type:` во frontmatter | auto-fix |
| 13.2 | Битые строки в `index.md` (ссылка на удалённую страницу) | auto-fix |
| 13.3 | Страница есть, строки в `index.md` нет | ask |
| 13.4 | `domain:` ссылается на несуществующую domain-страницу | ask |
| 13.5 | Асимметричные `related:` (A→B без B→A) | ask |
| 13.6 | Бинарный источник вне `raw/formats/` | ask |
| 13.7 | Path-prefixed wikilinks (`[[wiki/X/Y]]` вместо `[[Y]]`) | auto-fix |
| 14 | Пустые секции | skip |
| 15 | Стилистические нарушения | skip |
| 16 | `similar-but-unlinked` (только `--approx`) | ask |
| 17 | `synthesis-drift` (только `--approx`) | ask |

---

## Конвенции

| Элемент | Конвенция | Пример |
|---|---|---|
| Имена файлов | Title Case с пробелами | `Reward Model.md` |
| Папки | lowercase | `wiki/ideas/`, `wiki/entities/` |
| Теги | аббревиатуры заглавными, обычные слова с заглавной первой буквы | `ML`, `RL`, `Alignment` |
| Wikilinks | точно совпадают с именем файла | `[[Reward Model]]` |
| `[[raw/...]]` | без расширения `.md`, только в `sources` frontmatter | `[[raw/RLHF]]` |

Имена файлов уникальны по всему vault — wikilinks без путей работают только при уникальности.

---

## Что lint не делает

- Не правит content-файлы. Никогда.
- Не создаёт стаб-страницы. Не предлагает создавать в `auto-fix` категории — это всегда `ask`.
- Не удаляет файлы. Сирот только флагает.
- Не разрешает противоречия. Только описывает.
- **Не проверяет порядок доменов скриптовой эвристикой.** Convention «от частного к общему» проверяется в Layer 2 (LLM) — там агент использует семантическое знание о соотношении доменов. Скриптовые подсчёты member-pages здесь ненадёжны (молодая wiki, новые широкие домены). См. секцию «Domain-order» выше.

Все правки делает `ingest` (через Phase 8 после синтеза или через `/ingest --fix` вне цикла).
