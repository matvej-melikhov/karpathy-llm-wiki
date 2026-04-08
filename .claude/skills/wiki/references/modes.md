# Режимы wiki

Четыре режима покрывают основные сценарии. При INIT пользователь выбирает один. Режимы можно комбинировать.

---

## Режим A: CodeBase

Используется когда: "карта кодовой базы", "архитектурная wiki для проекта", "понять этот репозиторий"

Структура:

```
vault/
├── raw/              # README, выгрузки git log, дампы кода, экспорты issues
├── wiki/
│   ├── modules/       # одна заметка на каждый крупный модуль / пакет / сервис
│   ├── components/    # переиспользуемые компоненты
│   ├── decisions/     # Architecture Decision Records (ADRs)
│   ├── dependencies/  # внешние зависимости, версии, оценка рисков
│   └── flows/         # потоки данных, request paths, auth flows
└── CLAUDE.md
```

Frontmatter для `wiki/modules/`:

```yaml
---
type: module           # module | component | decision | dependency | flow
path: "src/auth/"
status: in-progress    # evaluation | in-progress | ready
language: typescript
purpose: ""
maintainer: ""
last_updated: 2026-04-29
linked_issues: []
depends_on: []
used_by: []
tags:
  - module
created: 2026-04-29
updated: 2026-04-29
---
```

Ключевые страницы для создания: `[[Архитектурный обзор]]`, `[[Поток данных]]`, `[[Технологический стек]]`, `[[Граф зависимостей]]`, `[[Ключевые решения]]`

---

## Режим B: SecondBrain

Используется когда: "личная база знаний", "отслеживание целей", "синтез журнала", "wiki для жизни"

Структура:

```
vault/
├── raw/              # дневниковые записи, статьи, заметки по подкастам, голосовые транскрипты
├── wiki/
│   ├── goals/         # личные и профессиональные цели с прогрессом
│   ├── learning/      # концепции в освоении, развитие навыков
│   ├── people/        # отношения, общий контекст, follow-ups
│   ├── areas/         # сферы жизни: здоровье, финансы, карьера, творчество
│   └── resources/     # книги, курсы, инструменты для повторного использования
└── CLAUDE.md
```

Frontmatter для `wiki/goals/`:

```yaml
---
type: goal             # goal | concept | person | area | resource | reflection
status: in-progress    # evaluation | in-progress | ready
area: career           # health | career | finance | creative | relationships | growth
priority: 1
target_date: 2026-12-31
progress: 0            # 0-100 процентов
tags:
  - goal
created: 2026-04-29
updated: 2026-04-29
---
```

Ключевые страницы: `[[Главная цель]]`, `[[Шаблон еженедельного ревью]]`, `[[Годовые цели]]`

---

## Режим C: Research

Используется когда: "исследовательская wiki по [теме]", "отслеживание читаемых статей", "построение тезиса"

Структура:

```
vault/
├── raw/              # PDF, веб-клипы, файлы данных, сырые заметки
├── wiki/
│   ├── papers/        # саммари статей с ключевыми утверждениями и методологией
│   ├── ideas/         # извлечённые идеи, модели, фреймворки
│   ├── entities/      # люди, организации, методы, датасеты
│   ├── thesis/        # развивающийся синтез: страницы "состояния области"
│   └── gaps/          # открытые вопросы, противоречия, нужны исследования
└── CLAUDE.md
```

Frontmatter для `wiki/papers/`:

```yaml
---
type: paper            # paper | idea | entity | thesis | gap
status: in-progress    # evaluation | in-progress | ready
year: 2024
authors: []
venue: ""
key_claim: ""
methodology: ""
contradicts: []
supports: []
tags:
  - paper
created: 2026-04-29
updated: 2026-04-29
---
```

Ключевые страницы: `[[Обзор исследования]]`, `[[Карта ключевых утверждений]]`, `[[Открытые вопросы]]`, `[[Сравнение методологий]]`

---

## Режим D: Resource

Используется когда: "wiki-компаньон для книги", "конспект курса", "по мере чтения [название]"

Структура:

```
vault/
├── raw/              # конспекты глав, выделения, упражнения
├── wiki/
│   ├── characters/    # персонажи, эксперты, личности (адаптировать под контент)
│   ├── themes/        # основные темы с подтверждающими свидетельствами
│   ├── ideas/         # доменные термины и фреймворки
│   ├── timeline/      # структура сюжета, последовательность учебной программы, карта глав
│   └── synthesis/     # личные выводы, вопросы, применение
└── CLAUDE.md
```

Frontmatter для `wiki/ideas/`:

```yaml
---
type: idea             # idea | character | theme | chapter | synthesis
status: in-progress    # evaluation | in-progress | ready
source_chapters: []
first_appearance: ""
tags:
  - idea
created: 2026-04-29
updated: 2026-04-29
---
```

Ключевые страницы: `[[Обзор книги]]`, `[[Карта тем]]`, `[[Указатель персонажей / экспертов]]`, `[[Мои выводы]]`

---

## Комбинирование режимов

Режимы можно сочетать. Примеры:

- "GitHub-репо + исследование используемого AI-подхода" → CodeBase + Research (`papers/`)
- "Личная база + книжный конспект" → SecondBrain + Resource (`themes/`, `synthesis/`)
- "Исследование с курсом" → Research + Resource (`timeline/`, `synthesis/`)

При комбинировании сохраняй имена папок различимыми. Не смешивай папки с одинаковыми именами из разных режимов в одну.
