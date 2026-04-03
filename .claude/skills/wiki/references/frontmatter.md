# Схема frontmatter

Каждая wiki-страница начинается с YAML frontmatter. Только плоский YAML, без вложенности (этого требует Obsidian Properties UI).

---

## Универсальные поля

Обязательны на каждой странице:

```yaml
---
type: <source|entity|idea|question|overview|meta>
title: "Читаемое название"
created: 2026-04-29
updated: 2026-04-29
tags:
  - <тег-домена>
  - <тег-типа>
status: <evaluation|in-progress|ready>
related:
  - "[[Другая страница]]"
sources:
  - "[[.raw/articles/source-file.md]]"
---
```

### Значения status

- `evaluation` — страница на оценке: создана, но содержание ещё проверяется или дорабатывается
- `in-progress` — активно развивается, есть реальный контент, но не завершена
- `ready` — готова, исчерпывающая, хорошо слинкована

---

## Тип-специфичные поля

### source (источник)

Добавляются после универсальных:

```yaml
source_type: article    # article | video | podcast | paper | book | transcript | data
author: ""
date_published: 2026-04-29
url: ""
confidence: high        # high | medium | low
key_claims:
  - "Первое ключевое утверждение"
  - "Второе ключевое утверждение"
```

### entity (сущность)

```yaml
entity_type: person     # person | organization | product | repository | place
role: ""
first_mentioned: "[[Источник]]"
```

### idea (идея, концепция)

```yaml
complexity: intermediate  # basic | intermediate | advanced
domain: ""
aliases:
  - "альтернативное название"
  - "аббревиатура"
```

### question (сохранённый ответ)

```yaml
question: "Оригинальный запрос"
answer_quality: solid   # draft | solid | definitive
```

---

## Правила

1. Только плоский YAML. Никаких вложенных объектов.
2. Даты как строки `YYYY-MM-DD`, не ISO datetime.
3. Списки в формате `- элемент`, не inline `[a, b, c]`.
4. Wikilinks внутри YAML обязательно в кавычках: `"[[Страница]]"`.
5. Поля `related` и `sources` содержат wikilinks, а не сырые URL.
6. Поле `updated` обновляется при каждом изменении контента страницы.
