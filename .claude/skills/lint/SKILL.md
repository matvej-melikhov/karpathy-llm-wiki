---
name: lint
description: >
  Ревьюер Obsidian wiki-vault. Запускает статические проверки + script auto-fixes,
  затем агентские проверки (Layer 2) и LLM-fix'ы, в конце ведёт диалог с
  пользователем по оставшимся ask-issues. Единственный owner всего post-detection
  пайплайна — вызывается напрямую (`/lint`) или из ingest в конце synthesis-цикла.
  Триггеры: /lint, "lint", "проверка здоровья", "очисти wiki", "проверь wiki",
  "найди сирот", "wiki audit".
---

# lint: пайплайн ревью wiki

Lint — единственный owner всего пайплайна обработки issues. Когда скилл вызван
(пользователем напрямую или из ingest), он:

1. Запускает `bin/static_lint.py` (Layer 1 + script auto-fixes inline).
2. Запускает Layer 2 — LLM-семантические проверки.
3. Применяет agent auto-fixes (`missing-summary`, `domain-order`, `tag-casing`).
4. Ведёт диалог с пользователем по оставшимся ask-issues.
5. Перезаписывает `lint-state.json` финальным состоянием.

После выполнения: либо все issues разрешены (open_issues пуст), либо остались
явно отложенные пользователем («позже»).

---

## Pipeline

### Step 1. Запустить `bin/static_lint.py`

```bash
python3 bin/static_lint.py          # --quick (default)
python3 bin/static_lint.py --full   # full audit
```

Скрипт сам делает:
- skip-check (если wiki не менялась и нет накопленных issues — пропуск)
- детект 13 типов issues (Layer 1) + 2 embedding-based (Layer 1.5)
- **inline применение script auto-fixes** для всех script-fixable типов
- запись `lint-state.json` с remaining issues (agent-fix + ask + skip)

Embedding-based проверки (`similar-but-unlinked`, `synthesis-drift`,
`contradiction_candidates`) запускаются всегда, если есть `wiki/meta/embeddings.json`.
Если эмбеддингов нет (Ollama не запущена) — warning + skip только этих
checks, остальные работают.

В режиме `--quick` (по умолчанию) скрипт фильтрует scope: новые issues
эмитятся только для touched pages (контент-хеш изменился с прошлого lint),
старые issues для не-touched сохраняются. В `--full` scope не ограничен.

### Step 2. Прочитать `lint-state.json`

Получить:
- `open_issues` — всё что не пофиксил скрипт
- `contradiction_candidates` (опц.) — пары для Layer 2

Если `open_issues` пуст и `contradiction_candidates` нет — pipeline закончен,
ничего больше делать не нужно. Сообщить пользователю «wiki чистая».

### Step 3. Layer 2 — LLM-проверки

См. секцию «Layer 2» ниже. Дописывает свои issues в `open_issues` (в памяти,
запись на диск только в Step 6).

### Step 4. Agent auto-fixes

Для issues типа `missing-summary`, `domain-order`, `tag-casing` (см. секцию
«Agent auto-fixes» ниже) применить LLM-генерируемые правки. Удалить
применённые из `open_issues`.

### Step 5. Ask-dialogue

Оставшиеся ask-issues спросить у пользователя одним батчем (см. «Ask-dialogue»).
По каждому ответу применить соответствующее действие, удалить из `open_issues`.
Issues с ответом «отложить» остаются в `open_issues`.

### Step 6. Записать финальное состояние

Если в Step 3-5 что-то поменялось — пересчитать `wiki_hash` и записать
`lint-state.json` с обновлённым `open_issues`. Иначе оставить как есть.

---

## Layer 2: LLM-проверки

Layer 2 запускается **этим скиллом** после Layer 1+1.5. Каждая проверка ниже —
самостоятельное задание: дано **что найти**, **формат issue**, **граничные
случаи**. Способ исполнения (как читать страницы, в каком порядке, в одном
батче или нескольких) — на твоё усмотрение. Используй доступные артефакты
(перечислены в каждой секции) и применяй естественный triage.

### `domain-order`

**Цель:** в поле `domain:` домены должны быть упорядочены от частного к
общему. Первый = primary classification, используется для раскраски в
knowledge map.

**Что НЕ флагуем:**
- Параллельные/cross-cutting домены без иерархии: `[Machine Learning, Knowledge Management]` — оба корневые, иерархии нет.
- Один домен — упорядочивать нечего.

**Issue format:**
```json
{
  "type": "domain-order",
  "where": "wiki/ideas/PPO.md",
  "current": ["Machine Learning", "Reinforcement Learning"],
  "expected": ["Reinforcement Learning", "Machine Learning"],
  "reasoning": "RL ⊂ ML"
}
```

Это знание о мире (RL — поддомен ML), а не структурное свойство wiki.
Скриптовые подсчёты member-pages ломаются на молодой wiki, поэтому LLM.

### `tag-casing`

**Цель:** теги во frontmatter с правильным регистром. Аббревиатуры
uppercase (`ML`, `RL`, `IR`, `NLP`, `RLHF`), обычные слова TitleCase
(`Code`, `Optimization`, `Alignment`).

**Что флагуем:**
- Lowercase аббревиатуры: `ml` → `ML`, `rl` → `RL`.
- Lowercase обычные слова: `optimization` → `Optimization`.
- Очевидные опечатки регистра: `mL` → `ML`.

**Что НЕ флагуем:**
- Уже-uppercase аббревиатуры: `ML`, `RL`, `IR`, `RLHF`.
- Capitalized обычные слова: `Code`, `Optimization`, `Math`, `Alignment`.
- Канонические аббревиатуры с нестандартным регистром: `LoRA`, `MapReduce`, `KMeans`.

**Issue format** (один issue на тег):
```json
{
  "type": "tag-casing",
  "where": "wiki/ideas/X.md",
  "current": "ml",
  "expected": "ML",
  "reasoning": "ML — аббревиатура (Machine Learning)"
}
```

### `contradiction`

**Цель:** найти пары страниц с прямо противоречивыми утверждениями (одна
говорит X, другая ¬X на ту же тему).

**Что НЕ контрадикция:**
- Дополняющие утверждения («A это X», «A также Y»).
- Утверждения на разные подтемы.
- Несогласованности в формулировках без логического противоречия.

**Доступные артефакты:**
- `lint-state.json::contradiction_candidates` — список пар, отфильтрованный
  Layer 1.5 по cosine similarity. Это primary entry point — пары, у которых
  даже теоретически может быть противоречие. Если поле есть — начинай
  отсюда.
- Если нет (эмбеддинги не доступны) — построй кандидатов сам через
  topic-overlap (общие домены, общие теги, упоминания друг друга).

**Issue format:**
```json
{
  "type": "contradiction",
  "page_a": "wiki/ideas/A.md",
  "page_b": "wiki/ideas/B.md",
  "claim": "<одно предложение про что они расходятся>"
}
```

### `outdated-claim`

**Цель:** утверждение в более старой странице опровергнуто более новой.

Проверяй только пары где даты `updated:` различаются и темы пересекаются.
Можно использовать те же `contradiction_candidates`. Дополнительная
эвристика: A.updated < B.updated, B содержит явное «X has been replaced»,
«Y is no longer recommended», «previously, but now» паттерны.

**Issue format:**
```json
{
  "type": "outdated-claim",
  "where": "wiki/ideas/A.md",
  "claim": "<устаревшее утверждение в A>",
  "conflicts_with": "wiki/ideas/B.md"
}
```

### `missing-concept`

**Цель:** концепция упомянута в **≥3 разных content-страницах**, но своей
wiki-страницы у неё нет — кандидат на создание.

**Что считается «упоминанием»:**
- Capitalized one-word terms (аббревиатуры, имена методов): `GAE`, `LoRA`, `BERT`.
- Multi-word concepts: `Monte Carlo`, `Temporal Difference Learning`,
  `Markov Decision Process`.

**Что НЕ считается упоминанием:**
- Wikilinks (`[[GAE]]`) — отдельный case, ловится `dead-link`.
- Code blocks (fenced ``` или inline `code`) — это примеры/код, не нарратив.
- Meta-страницы (`wiki/cache.md`, `wiki/log.md`, `wiki/index.md`).

**Threshold:** 3 разных страницы (не 3 упоминания). Повторное упоминание в
одной странице не накручивает.

**Доступная эвристика:** Bash-grep по capitalized phrases, sort | uniq -c,
top кандидатов — дешёвый pre-filter. Дальше LLM судит «реальный концепт
достойный страницы или общеупотребительное слово».

**Issue format:**
```json
{
  "type": "missing-concept",
  "term": "GAE",
  "mentioned_in": ["wiki/ideas/PPO.md", "wiki/ideas/TD Learning.md",
                    "wiki/ideas/Advantage Function.md"]
}
```

---

## Agent auto-fixes

Применяются **автоматически** (без вопросов пользователю), но требуют LLM.

### `missing-summary`

Контент-страница без `summary:` во frontmatter (либо `summary: ""`).

**Действие:**
1. Прочитать страницу целиком.
2. Сгенерировать саммари ≤120 символов: одно декларативное предложение, что
   это и зачем. Без отсылок к источнику, без «эта страница описывает...».
3. Вписать в frontmatter одинарными YAML-кавычками: `summary: 'текст'`.
   Двойные кавычки только если внутри есть одинарная. Внутри текста допустимы
   `:`, `$`, `\`, диакритика — одинарные кавычки безопасны.
4. На следующем Stop-hook'е `bin/gen_index.py` подхватит саммари в
   `wiki/index.md`.
5. Удалить issue из `open_issues`.

Если страница пустая (нет body) — взять тему из заголовка/aliases, описать
максимально кратко.

### `domain-order`

Issue эмитится Layer 2 (см. выше), здесь применяем.

**Действие:**
1. Прочитать `domain:` блок из frontmatter `where`.
2. Переписать в порядке из `expected` (массив имён в правильном порядке).
3. Сохранить wikilink-формат (`"[[Domain Name]]"`) — поменять только порядок.
4. Удалить issue из `open_issues`.

### `tag-casing`

Issue эмитится Layer 2 (см. выше), применяем здесь.

**Действие:**
1. Прочитать `tags:` массив из `where`.
2. Заменить `current` на `expected`.
3. Удалить issue из `open_issues`.

---

## Ask-dialogue

Оставшиеся issues (тип ≠ auto-fix и ≠ agent-fix) спросить у пользователя одним
батчем. Категории см. в таблице ниже.

### Формат вопроса

```
Lint нашёл проблемы, требующие решения:

1. [dead-link] [[SFT]] упомянута в [[RLHF]], страницы нет.
   → создать заглушку / убрать ссылку / отложить?

2. [orphan] [[Foo Bar]] никем не упомянута.
   → удалить / слинковать с [[X]] / отложить?

3. [missing-concept] "GAE" в [[PPO]], [[TD Learning]], [[Advantage]].
   → создать idea-страницу / отложить?

4. [contradiction] [[A]] и [[B]] (similarity 0.87): <claim>
   → разрешить (какая страница права?) / отложить?

5. [asymmetric-related] [[A]] → [[B]], но [[B]] не ссылается на [[A]].
   → симметризовать / удалить одностороннюю / отложить?

6. [similar-but-unlinked] [[PPO]] и [[Policy Gradient]] (cosine 0.87)
   семантически близки, но wikilink между ними отсутствует.
   → связать в обе стороны / связать в одну / игнорировать / отложить?

7. [synthesis-drift] [[RLHF]] (drift 0.42) сильно отклонилась от
   эмбеддинга своих источников. Возможна галлюцинация.
   → перечитать страницу и сравнить с источником / отложить?
```

### Действия по ответам

- **«Создать [[X]]»** (`dead-link`, `missing-concept`) → создать stub-страницу
  по `_templates/idea.md` (или `entity` если контекст указывает) с минимальным
  содержанием, удалить issue.
- **«Убрать ссылку»** (`dead-link`) → удалить wikilink из тела родительской
  страницы, удалить issue.
- **«Удалить»** (`orphan`) → удалить файл целиком, удалить issue.
- **«Слинковать с [[X]]»** (`orphan`) → дописать wikilink на текущую страницу
  в подходящую родительскую (выбрать `X` агентски), удалить issue.
- **«Симметризовать»** (`asymmetric-related`) → дописать `[[A]]` в `related:`
  страницы B, удалить issue.
- **«Удалить одностороннюю»** (`asymmetric-related`) → удалить `[[B]]` из
  `related:` страницы A, удалить issue.
- **«Создать domain»** (`dangling-domain-ref`) → создать
  `wiki/domains/<missing_domain>.md` из `_templates/domain.md` с минимальным
  описанием, удалить issue.
- **«Убрать domain»** (`dangling-domain-ref`) → удалить
  `[[<missing_domain>]]` из поля `domain:` страницы, удалить issue.
- **«Связать обе»** (`similar-but-unlinked`) → добавить `[[B]]` в `related:` A
  И `[[A]]` в `related:` B, удалить issue.
- **«Связать одну»** (`similar-but-unlinked`) → спросить направление, добавить
  wikilink в `related:` соответствующей страницы, удалить issue.
- **«Игнорировать»** (`similar-but-unlinked`) → удалить issue (могут быть
  параллельные сущности, связь не нужна).
- **«Разрешить»** (`contradiction`, `outdated-claim`) → агент применяет
  правильное утверждение к одной из страниц или к обеим, удалить issue.
- **«Перечитать»** (`synthesis-drift`) → прочитать страницу + связанные
  `[[raw/...]]`, сверить, при необходимости обновить, удалить issue.
- **«Отложить» / «позже»** → оставить issue в `open_issues`. Снова всплывёт
  при следующем `/lint`.

### Несколько issues разом

Пользователь может ответить пачкой («1 — создать, 2 — отложить, 3, 4 —
симметризовать»). Применить каждый ответ к соответствующему issue.

---

## Категории issues

Каждый issue имеет `type` — категория определяет, на каком шаге pipeline он
обрабатывается.

### Script auto-fix (Step 1, inline в `static_lint.py`)

Применяется самим скриптом, не доходит до lint-скилла. Перечислено для
контекста.

| `type` | Условие | Структура issue |
|---|---|---|
| `status-not-in-enum` | `status` не из `evaluation/in-progress/ready` | `{type, where, value, fix}` |
| `invalid-fields` | frontmatter не соответствует `_templates/<type>.md`. Subtype `extra` (удалить поле) или `missing` (добавить с default из шаблона) | `{type, where, subtype, field}` |
| `inline-tags` | `tags: [a, b]` инлайн вместо block YAML | `{type, where}` |
| `raw-link-with-extension` | `[[raw/X.md]]` вместо `[[raw/X]]` | `{type, where, link}` |
| `raw-ref-in-body` | `[[raw/...]]` в теле страницы | `{type, where, link, line}` |
| `folder-type-mismatch` | `wiki/<X>/` vs `type:` рассогласованы | `{type, where, current_type, expected_type}` |
| `non-canonical-wikilink` | path-prefixed `[[wiki/ideas/X]]` вместо `[[X]]` | `{type, where, link, fix, context}` |
| `binary-source-outside-formats` | бинарь в `raw/` вне `raw/formats/` | `{type, where, suggested}` |

### Agent auto-fix (Step 4)

Требует LLM (генерация контента или семантическое суждение). См. секцию выше.

| `type` | Условие | Структура |
|---|---|---|
| `missing-summary` | content-страница без `summary:` | `{type, where, page_type}` |
| `domain-order` | LLM-issue, см. Layer 2 | `{type, where, current, expected, reasoning}` |
| `tag-casing` | LLM-issue, см. Layer 2 | `{type, where, current, expected, reasoning}` |

### Ask user (Step 5)

Требует решения пользователя.

| `type` | Условие | Структура |
|---|---|---|
| `dead-link` | wikilink на несуществующую страницу | `{type, where, what, context}` |
| `orphan` | страница без входящих wikilinks | `{type, where}` |
| `missing-concept` | концепция в ≥3 страницах без своей page | `{type, term, mentioned_in}` |
| `contradiction` | противоречие двух страниц | `{type, page_a, page_b, claim}` |
| `outdated-claim` | утверждение опровергнуто более новой | `{type, where, claim, conflicts_with}` |
| `dangling-domain-ref` | `domain:` на несуществующую domain-страницу | `{type, where, missing_domain}` |
| `asymmetric-related` | A.related → B без B.related → A | `{type, page_a, page_b}` |
| `similar-but-unlinked` | две страницы близки, wikilink отсутствует (если эмбеддинги доступны) | `{type, page_a, page_b, similarity, threshold}` |
| `synthesis-drift` | страница ушла далеко от центроида источников (если эмбеддинги доступны) | `{type, where, drift, threshold}` |

### Skip

Информационные флаги, не спрашиваем:

| `type` | Условие |
|---|---|
| `style-nit` | стилистические замечания (не декларативное настоящее, отсутствие линка на не-ключевую сущность) |

---

## Команда и флаги

| Команда | Поведение |
|---|---|
| `/lint` | **--quick (default)**: skip-check по wiki_hash; новые issues эмитятся только для touched pages (контент-хеш изменился с прошлого lint); старые issues для не-touched сохраняются. Embedding checks (similar-but-unlinked, synthesis-drift) запускаются всегда если есть `wiki/meta/embeddings.json`. |
| `/lint --full` | Полный audit. Skip-check игнорируется, scope не ограничен. Используется periodically для глубокой проверки (раз в неделю, или после массового рефактора). |

Touched detection: `wiki_hash` сохраняется как aggregate, `page_hashes` —
per-page sha256. На первом запуске после миграции state'а bootstrap трактует
все страницы как touched (де-факто = `--full`).

---

## Конвенции wiki

См. `.claude/skills/wiki/references/frontmatter.md` — schema, теги, regex
имён файлов и т.д. Здесь не дублируем.

---

## Что lint не делает

- **Не лезет в `raw/`.** raw-источники иммутабельны (кроме transcript-конвертации
  через `transcribe`).
- **Не правит content-файлы по ask-issues самостоятельно.** Только после
  явного решения пользователя.
- **Не запускает синтез.** Это работа `ingest`. lint только чинит.
- **Не удаляет файлы без явного «удалить» от пользователя** (на orphan).