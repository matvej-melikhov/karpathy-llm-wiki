---
type: domain
created: <% tp.date.now("YYYY-MM-DD") %>
updated: <% tp.date.now("YYYY-MM-DD") %>
tags: []
status: in-progress
domain:
  - "[[<% tp.file.title %>]]"
related: []
---

# <% tp.file.title %>

<!-- Краткое описание области: что в неё входит, какие подтемы охватывает. -->

## Ключевые концепции

<!-- 3-5 главных страниц домена, вручную выбранных. -->

## Все страницы домена

<!-- Создай файл `wiki/meta/dashboards/<% tp.file.title %>.base` со следующим содержимым,
     затем embed через `![[<% tp.file.title %>.base]]` (Obsidian резолвит
     по уникальному basename, путь не нужен).

filters:
  and:
    - file.inFolder("wiki/")
    - file.hasLink("<% tp.file.title %>")
    - not:
        - file.inFolder("wiki/domains/")
        - file.inFolder("wiki/meta/")
views:
  - type: table
    name: "Все страницы"
    order:
      - file.name
      - type
      - status
      - tags
      - updated
    groupBy:
      property: type
      direction: ASC
-->

![[<% tp.file.title %>.base]]
