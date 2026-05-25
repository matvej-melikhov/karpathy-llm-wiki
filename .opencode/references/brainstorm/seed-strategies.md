# Seed strategies

Подробный алгоритм выбора seed для brainstorm-сессии. Подключается из основной команды `.opencode/commands/brainstorm.md`, раздел Pre-flight checks.

---

## Explicit seed

Пользователь задаёт тему явно: либо текстом (`/brainstorm "Когда ingest создаёт шум"`), либо wikilink на существующую страницу (`/brainstorm [[YetiRank]]`).

**Алгоритм:**

1. Если аргумент — wikilink, проверь, что страница существует. Если нет — сообщи и предложи альтернативы (Glob по `wiki/`).
2. Запусти dedup-проверку по seed:
   ```bash
   python3 bin/embed.py query "<seed text>" -k 5
   ```
3. Если в топ-5 есть `wiki/minds/...md` с similarity >0.7 — переход в **dedup dialog** (см. `.opencode/commands/brainstorm.md` → Pre-flight checks → Dedup).
4. Иначе — стартуем сессию с заявленным seed.

---

## Bridge-gap seed

Пользователь не задал тему. Цель — найти пару страниц, которые семантически близки, но не связаны wikilink. Это «потенциальные мосты», на которых рождаются неочевидные связи.

**Алгоритм:**

1. Загрузить эмбеддинги:
   ```bash
   python3 bin/embed.py stats
   ```
   Если индекс пуст или embed-сервис недоступен — сообщи и предложи `/brainstorm --random` или explicit seed.

2. **Получить пары-кандидаты.** Использовать существующий `bin/static_lint.py --check similar-but-unlinked` или собрать через эмбеддинги напрямую (если хочется большую гибкость порогов):

   ```python
   from embed import EmbedIndex, WIKI_EMBED_PATH, cosine
   from static_lint import discover_pages, _build_link_graph

   pages = discover_pages()
   idx = EmbedIndex(WIKI_EMBED_PATH); idx.load()
   outbound, _ = _build_link_graph(pages)
   by_name = {p.name: p for p in pages if p.folder in ("ideas", "entities", "domains") and p.page_type != "meta"}

   pairs = []
   names = [n for n in idx.items if n in by_name]
   for i, a in enumerate(names):
       for b in names[i+1:]:
           sim = cosine(idx.items[a].vec, idx.items[b].vec)
           if sim < 0.6:
               continue
           if b in outbound.get(a, set()) or a in outbound.get(b, set()):
               continue
           # Фильтр: хотя бы одна страница — idea
           if by_name[a].page_type != "idea" and by_name[b].page_type != "idea":
               continue
           pairs.append((a, b, sim))

   pairs.sort(key=lambda x: -x[2])
   top3 = pairs[:3]
   ```

3. **Если кандидатов <3** — fallback:
   - сообщи пользователю «нашёл только N кандидатов»;
   - если 1-2 — предложи их;
   - если 0 — fallback на `/brainstorm --random`.

4. **Показать пользователю** как нумерованный список:

   ```
   1. [[A]] × [[B]] (similarity 0.78) — обе про X, но не связаны
   2. [[C]] × [[D]] (similarity 0.74)
   3. [[E]] × [[F]] (similarity 0.71)
   ```

   Краткое объяснение «почему резонирует» — опционально, по одному короткому наблюдению на пару (≤10 слов).

5. **Выбор пользователя.** Возможные ответы:
   - номер (1-3) → стартуем сессию с `seed_strategy: bridge`, `seed: "A × B"`, `seed_pages: [[[A]], [[B]]]`
   - «свою тему» → переход на explicit
   - «random» → fallback на random

6. **Особенности bridge-сессии для divergence loop.** В Setup phase агент озвучивает:

   > Тема: связь между [[A]] и [[B]]. Они семантически рядом, но wikilink между ними нет. Цель — найти эту связь словами. С чего начинаешь?

   Probings в bridge-режиме чуть отличаются от explicit:
   - «что общего у A и B, что не очевидно из их страниц?»
   - «если эту связь сделать в одно предложение, как звучит?»
   - «какая третья вещь делает эту связь объяснимой?»

---

## Random seed

Минимальная стратегия — случайная страница из content-папок (idea/entity/domain). Mind-страницы и questions исключены: их по природе уже видели в `/brainstorm` и `/save`.

**Алгоритм:**

1. Glob `wiki/{ideas,entities,domains}/*.md`.
2. `random.choice` — одна страница.
3. Стартуем сессию с `seed_strategy: random`, `seed: "[[Имя]]"`, `seed_pages: [[[Имя]]]`.

Никаких dedup-проверок: random не требует совпадения с темой пользователя.

---

## `--continue` seed

Пользователь явно продолжает существующую mind (`/brainstorm --continue [[Имя]]`).

**Алгоритм:**

1. Аргумент **обязателен**. Без — ошибка с подсказкой:

   > `--continue` требует имя mind. Пример: `/brainstorm --continue [[Шум в ingest]]`

2. Read `wiki/minds/<имя>.md`. Если не существует — ошибка с предложением альтернатив.

3. В Setup phase агент озвучивает:

   > Продолжаем [[<имя>]] (status: <draft/stable>). Текущие тезисы: <свод по абзацам, 1 строка на тезис>. Текущие related: <список>. Что добавляем?

4. Дальше divergence loop как обычно. На стадии commit — переход в **continuation flow → Develop existing** или **New revision** (по выбору пользователя на финальном preview-gate).

---

## Что если bridge даёт кандидатов с разных доменов

Это норма и обычно желательно — кросс-доменные связи самые ценные. Алгоритм не штрафует кросс-домен. Но если у пары есть общий `domain:` в frontmatter — сделать это observable в показе:

```
1. [[Reward Hacking]] × [[LLM Wiki Pattern]] (0.78) — кросс-домен (RL ↔ KM)
2. [[Bradley-Terry]] × [[PPO]] (0.72) — RL внутри
```

Это просто помогает пользователю выбрать тип ассоциации. Никакой технической разницы нет.
