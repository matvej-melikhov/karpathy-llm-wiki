# Phase 8 — Lint review и Fix-only режим

Один и тот же блок логики используется в двух точках входа:

| Точка входа | Когда |
|---|---|
| **Phase 8** | После Phase 7 в обычном synthesis cycle (есть свежий источник) |
| **Fix-only mode** (`/ingest --fix`) | Без источника, для применения накопленных `open_issues` |

Оба читают `wiki/meta/lint-reports/lint-state.json`, применяют категоризацию, перезаписывают state.

---

## Phase 8 — Lint review (после synthesis)

`lint` — read-only ревьюер. Он анализирует wiki и пишет отчёт в `wiki/meta/lint-reports/lint-state.json`. **Все правки делает ingest по этому отчёту.** Это разделение: lint = static analysis, ingest = единственный writer.

### Шаги

> Эмбеддинги освежает Stop-hook (`python3 bin/embed.py update`) на завершении предыдущего turn'а — отдельно их обновлять здесь не нужно. Если `wiki/meta/embeddings.json` отсутствует (Ollama не запущена / модель эмбеддера не установлена) — `--approx` ниже просто упадёт с понятной ошибкой, остальной lint работает без него.

1. **Вызвать `lint --approx`.** Без флагов — он сам решает, делать ли skip-check; в нашем случае wiki только что менялась, поэтому будет full audit. По завершении в `wiki/meta/lint-reports/lint-state.json` лежит свежий список `open_issues`.

   ```bash
   python3 bin/lint.py --approx
   ```

   Флаг `--approx` включает embedding-based проверки `similar-but-unlinked` и `synthesis-drift` (см. `.claude/skills/lint/SKILL.md`). Если эмбеддингов нет — запусти без `--approx`.

2. **Прочитать `wiki/meta/lint-reports/lint-state.json`.** Получить `open_issues`. Каждый issue имеет поле `type` — категория проверки (см. `.claude/skills/lint/SKILL.md`).

3. **Разнести issues по трём корзинам:**

   | Категория | Действие |
   |---|---|
   | **auto-fix** | применить правку молча, удалить из `open_issues` |
   | **ask** | собрать в батч, спросить пользователя одним сообщением |
   | **skip** | оставить в `open_issues` без вопроса |

   Принадлежность типа к категории — таблица "Категории issues" в `.claude/skills/lint/SKILL.md`.

4. **Применить auto-fix молча** (см. таблицу ниже).

5. **Спросить пользователя по `ask`-issues одним батчем.**

6. **Skip-issues не трогать.** Они остаются в `open_issues` и появятся снова при следующем `/lint`.

7. **Финал — пере-вычислить `wiki_hash` и записать `lint-state.json`.**

После Phase 8 ingest завершён. Записать запись в `raw/meta/ingested.json` (см. `references/dedup.md`).

---

## Auto-fix правки

Применяются молча, без вопросов:

| `type` | Правка |
|---|---|
| `status-not-in-enum` | заменить `status` на значение из `fix` (обычно `in-progress`) |
| `status-on-entity` | удалить поле `status` из frontmatter |
| `legacy-field` | удалить поле (`title` / `complexity` / `first_mentioned`) |
| `lowercase-tags` | переписать tags с правильным регистром (см. `.claude/skills/wiki/references/frontmatter.md`) |
| `inline-tags` | переписать в блочный YAML |
| `raw-link-with-extension` | `[[raw/X.md]]` → `[[raw/X]]` |
| `raw-ref-in-body` | удалить wikilink из тела |
| `empty-sources-section` | удалить секцию целиком вместе с заголовком |
| `folder-type-mismatch` | переписать `type:` во frontmatter в значение `expected_type` (берётся из имени папки: `ideas`→`idea`, `entities`→`entity`, `questions`→`question`, `domains`→`domain`) |
| `non-canonical-wikilink` | заменить `link` на `fix` в файле `where`. Локация уточняется через `context` (`line N` для тела, `frontmatter related/domain` для frontmatter). Используй точечный Edit с `link` как старая строка и `fix` как новая |
| `domain-order` | переписать блок `domain:` во frontmatter `where` в порядке из `expected` (массив имён доменов от частного к общему). Сохранить wikilink-формат (`"[[Domain Name]]"`) — поменять только порядок строк. Issue приходит от Layer 2 (агент уже вынес семантическое суждение); если агент пропустил пару — значит, иерархии нет, не трогаем |
| `missing-summary` | прочитать первый абзац страницы (или, если страница пустая, поле `aliases` / заголовок), сгенерировать декларативное саммари ≤120 символов. Вставить во frontmatter как `summary: '...'` (одинарные YAML-кавычки, не двойные). При следующем Stop-hook'е `bin/gen_index.py` подхватит саммари в `wiki/index.md` |

После каждой правки удалить соответствующий issue из `open_issues`.

---

## Формат батч-вопроса для ask-issues

```
Lint после синтеза нашёл проблемы, требующие решения:

1. [dead-link] [[SFT]] упомянута в [[RLHF]], страницы нет.
   → создать заглушку / убрать ссылку / отложить?

2. [orphan] [[Foo Bar]] никем не упомянута.
   → удалить / слинковать с [[X]] / отложить?

3. [missing-concept] "GAE" в [[PPO]], [[TD Learning]], [[Advantage]].
   → создать idea-страницу / отложить?

4. [similar-but-unlinked] [[PPO]] и [[Policy Gradient]] (cosine 0.87)
   семантически близки, но wikilink между ними отсутствует.
   → связать в обе стороны / связать в одну / игнорировать / отложить?

5. [synthesis-drift] [[RLHF]] (drift 0.42) сильно отклонилась от
   эмбеддинга своих источников. Возможна галлюцинация в синтезе.
   → перечитать страницу и сравнить с источником / отложить?
```

По ответу пользователя:
- "Создать [[SFT]]" → создать заглушку, убрать issue из `open_issues`
- "Убрать ссылку" → удалить wikilink из тела родительской страницы, убрать issue
- "Создать domain" (для `dangling-domain-ref`) → создать `wiki/domains/<missing_domain>.md` из `_templates/domain.md`, заполнить минимальное описание; убрать issue
- "Убрать ссылку" (для `dangling-domain-ref`) → удалить `[[<missing_domain>]]` из `domain:` поля страницы
- "Симметрия" (для `asymmetric-related`) → дописать обратную ссылку `[[A]]` в `related:` страницы B
- "Удалить одностороннюю" (для `asymmetric-related`) → удалить `[[B]]` из `related:` страницы A
- "Связать обе" (для `similar-but-unlinked`) → добавить `[[B]]` в `related:` страницы A **и** `[[A]]` в `related:` страницы B
- "Связать одну" (для `similar-but-unlinked`) → спросить направление, добавить wikilink в `related:` соответствующей страницы
- "Игнорировать" (для `similar-but-unlinked`) → убрать issue (страницы могут быть похожи, но это не значит что нужна связь — например параллельные сущности)
- "Перечитать" (для `synthesis-drift`) → прочитать wiki-страницу и связанные `[[raw/...]]`, сверить ключевые утверждения. Если есть отклонения — обновить страницу. Убрать issue из `open_issues`
- "Отложить" / "позже" → оставить issue в `open_issues`
- На несколько issues разом — обработать каждый по соответствующему ответу

---

## Запись финального состояния

```json
{
  "wiki_hash": "<sha256 после всех правок>",
  "last_audit": "<timestamp>",
  "files_checked": <N>,
  "open_issues": [<те, что не закрыты на этом проходе>]
}
```

Пере-хешируем после auto-fix и пользовательских правок — иначе следующий skip-check сразу побьётся.

---

## Fix-only режим (`/ingest --fix`)

Точка входа для починки **вне** synthesis-цикла. Используется когда пользователь руками отредактировал wiki в Obsidian, запустил `/lint`, увидел замечания и хочет их применить.

### Триггер

`/ingest --fix` без аргумента-источника.

### Шаги

1. Прочитать `wiki/meta/lint-reports/lint-state.json`. Если файла нет — сообщить "lint ещё не запускался; запусти `/lint` сначала" и завершить.
2. Прочитать `open_issues`. Если список пуст — сообщить "ничего чинить" и завершить.
3. Применить ту же категоризацию, что в Phase 8:
   - **auto-fix** → молча
   - **ask** → батчем спросить пользователя
   - **skip** → оставить
4. Пере-вычислить `wiki_hash`, перезаписать `lint-state.json` с актуальным `open_issues`.

Это **тот же логический блок**, что Phase 8 — просто без предшествующего синтеза. Поэтому fix-only mode переиспользует ту же реализацию шагов 2–7 из Phase 8 (см. выше).

`raw/meta/ingested.json` в этом режиме не трогается — нет нового источника.
