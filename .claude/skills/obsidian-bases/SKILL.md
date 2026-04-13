---
name: obsidian-bases
description: "Создание и редактирование Obsidian Bases (.base файлов): нативный database-слой Obsidian для динамических таблиц, card views, list views, фильтров, формул. Триггеры: создай base, добавь base file, obsidian bases, base view, фильтр заметок, формула, database view, динамическая таблица."
allowed-tools: Read Write
---

# obsidian-bases: database-слой Obsidian

Obsidian Bases (запущен в 2025) превращает заметки vault в queryable динамические views: таблицы, карточки, списки. Определяются в `.base` файлах. Плагин не требуется — это core-фича Obsidian с v1.9.10.

Официальная документация: https://help.obsidian.md/bases/syntax

---

## Формат файла

`.base` файлы содержат валидный YAML. Корневые ключи: `filters`, `formulas`, `properties`, `summaries`, `views`.

```yaml
# Глобальные фильтры: применяются КО ВСЕМ views
filters:
  and:
    - file.hasTag("wiki")
    - 'status != "ready"'

# Вычисляемые свойства
formulas:
  age_days: '(now() - file.ctime).days.round(0)'
  status_icon: 'if(status == "ready", "✅", "🔄")'

# Переопределение display name для panel свойств
properties:
  status:
    displayName: "Статус"
  formula.age_days:
    displayName: "Возраст (дней)"

# Один или несколько views
views:
  - type: table
    name: "Все страницы"
    order:
      - file.name
      - type
      - status
      - updated
      - formula.age_days
```

---

## Фильтры

Фильтры выбирают, какие заметки появляются. Применяются глобально или per-view.

```yaml
# Один строковый фильтр
filters: 'status == "in-progress"'

# AND: все должны быть true
filters:
  and:
    - 'status != "ready"'
    - file.hasTag("wiki")

# OR: любой может быть true
filters:
  or:
    - file.hasTag("idea")
    - file.hasTag("entity")

# NOT: исключить совпадения
filters:
  not:
    - file.inFolder("wiki/meta")

# Вложенные
filters:
  and:
    - file.inFolder("wiki/")
    - or:
        - 'type == "idea"'
        - 'type == "entity"'
```

### Операторы фильтров

`==` `!=` `>` `<` `>=` `<=`

### Полезные функции фильтров

| Функция | Пример |
|---|---|
| `file.hasTag("x")` | Заметки с тегом `x` |
| `file.inFolder("path/")` | Заметки в папке |
| `file.hasLink("Note")` | Заметки, ссылающиеся на Note |

---

## Properties

Три типа:
- **Note properties**: из frontmatter — `status`, `type`, `updated`
- **File properties**: метаданные — `file.name`, `file.mtime`, `file.size`, `file.ctime`, `file.tags`, `file.folder`
- **Formula properties**: вычисляемые — `formula.age_days`

---

## Формулы

Определяются в `formulas:`. Используются как `formula.name` в `order:` и `properties:`.

```yaml
formulas:
  # Дней с момента создания
  age_days: '(now() - file.ctime).days.round(0)'

  # Дней до даты-свойства
  days_until: 'if(due_date, (date(due_date) - today()).days, "")'

  # Условный label
  status_icon: 'if(status == "ready", "✅", if(status == "in-progress", "🔄", "🌱"))'

  # Оценка количества слов
  word_est: '(file.size / 5).round(0)'
```

**Ключевое правило**: вычитание двух дат возвращает `Duration`, не число. Всегда обращайся к `.days` сначала:

```yaml
# КОРРЕКТНО
age: '(now() - file.ctime).days'

# НЕПРАВИЛЬНО: упадёт
age: '(now() - file.ctime).round(0)'
```

**Всегда защищай nullable свойства через `if()`**:

```yaml
# КОРРЕКТНО
days_left: 'if(due_date, (date(due_date) - today()).days, "")'
```

---

## Типы views

### Table

```yaml
views:
  - type: table
    name: "Wiki Index"
    limit: 100
    order:
      - file.name
      - type
      - status
      - updated
    groupBy:
      property: type
      direction: ASC
```

### Cards

```yaml
views:
  - type: cards
    name: "Галерея"
    order:
      - file.name
      - tags
      - status
```

### List

```yaml
views:
  - type: list
    name: "Быстрый список"
    order:
      - file.name
      - status
```

---

## Шаблоны для wiki vault

### Дашборд содержимого wiki (все non-meta страницы)

```yaml
filters:
  and:
    - file.inFolder("wiki/")
    - not:
        - file.inFolder("wiki/meta")

formulas:
  age: '(now() - file.ctime).days.round(0)'

properties:
  formula.age:
    displayName: "Возраст (дней)"

views:
  - type: table
    name: "Все страницы wiki"
    order:
      - file.name
      - type
      - status
      - updated
      - formula.age
    groupBy:
      property: type
      direction: ASC
```

### Индекс сущностей (люди, организации, репо)

```yaml
filters:
  and:
    - file.inFolder("wiki/entities/")
    - 'file.ext == "md"'

views:
  - type: table
    name: "Сущности"
    order:
      - file.name
      - entity_type
      - status
      - updated
    groupBy:
      property: entity_type
      direction: ASC
```

### Идеи по домену

```yaml
filters:
  and:
    - file.inFolder("wiki/ideas/")

views:
  - type: table
    name: "Идеи"
    order:
      - file.name
      - domain
      - status
      - tags
      - updated
    groupBy:
      property: domain
      direction: ASC
```

---

## Встраивание в заметки

```markdown
![[MyBase.base]]

![[MyBase.base#View Name]]
```

---

## Где сохранять

Храни `.base` файлы в `wiki/meta/` для дашбордов vault:
- `wiki/meta/dashboard.base` — основной view содержимого
- `wiki/meta/entities.base` — трекер сущностей
- `wiki/meta/domains.base` — обзор доменов и их связей

---

## Правила YAML quoting

- Формулы с двойными кавычками → оберни в одинарные: `'if(done, "Yes", "No")'`
- Строки с двоеточиями или спецсимволами → оберни в двойные: `"Status: Active"`
- Незаключённые в кавычки строки с `:` ломают YAML парсинг

---

## Чего НЕ делать

- Не используй `from:` или `where:`: это синтаксис Dataview, не Bases
- Не используй `sort:` на root уровне: сортировка per-view через `order:` и `groupBy:`
- Не клади `.base` файлы вне vault: они рендерятся только внутри Obsidian
- Не ссылайся на `formula.X` в `order:` без определения `X` в `formulas:`
