# Схема frontmatter

Каждая wiki-страница начинается с YAML frontmatter. Только плоский YAML, без вложенности (этого требует Obsidian Properties UI).

---

## Универсальные поля

Обязательны на каждой содержательной странице:

```yaml
---
type: <idea|entity|question|domain|meta>
title: "Читаемое название"
created: 2026-04-29
updated: 2026-04-29
tags:
  - <тематический-тег>
status: <evaluation|in-progress|ready>
domain:
  - "[[Domain Page]]"
related:
  - "[[Другая страница]]"
sources:
  - "[[raw/articles/source-file.md]]"
---
```

### Значения `status`

- `evaluation` — страница на оценке: создана, но содержание ещё проверяется или дорабатывается
- `in-progress` — активно развивается, есть реальный контент, но не завершена
- `ready` — готова, исчерпывающая, хорошо слинкована

### Поле `domain`

Список wikilinks на страницы из `wiki/domains/`. Создаёт явные рёбра в графе Obsidian — domain-страницы становятся узлами кластеризации. Может быть пустым списком: `domain: []`.

### Поле `tags`

Тематические теги (`ml`, `alignment`, `rl`). Не дублируй тип страницы (тип уже хранится в `type`). Используется для фильтрации и для триггера предложений domain-страниц.

---

## Тип-специфичные поля

### idea

Концепция, механизм, теория, паттерн.

```yaml
complexity: intermediate  # basic | intermediate | advanced
aliases:
  - "альтернативное название"
  - "аббревиатура"
```

### entity

Именованный объект реального мира.

```yaml
entity_type: person       # person | organization | product | repository | place | paper | model | dataset
role: ""
first_mentioned: "[[Источник]]"
```

### question

Сохранённый ответ на конкретный вопрос.

```yaml
question: "Оригинальный запрос"
answer_quality: solid     # draft | solid | definitive
```

### domain

Навигационный хаб (Map of Content) для области знаний.

```yaml
# дополнительных полей не требуется — title и tags определяют домен
```

### meta

Служебная страница (index, log, cache, summary, dashboard, lint-отчёт). Минимальный frontmatter:

```yaml
---
type: meta
title: ""
updated: 2026-04-29
---
```

---

## Правила

1. Только плоский YAML. Никаких вложенных объектов.
2. Даты как строки `YYYY-MM-DD`, не ISO datetime.
3. Списки в формате `- элемент`, не inline `[a, b, c]`.
4. Wikilinks внутри YAML обязательно в кавычках: `"[[Страница]]"`.
5. Поля `related`, `sources`, `domain` содержат wikilinks, а не сырые URL.
6. Поле `updated` обновляется при каждом изменении контента страницы.
7. Поле `domain` ссылается только на страницы из `wiki/domains/`.
