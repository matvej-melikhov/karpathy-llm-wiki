#!/usr/bin/env bash
# setup-vault.sh — первичная настройка Obsidian-конфигурации vault.
#
# Создаёт минимальный набор .obsidian/ файлов, чтобы при первом открытии
# vault в Obsidian отображался корректно: отфильтрованный graph view,
# исключения file explorer, базовые настройки appearance.
#
# Запускается один раз перед первым открытием vault в Obsidian.
#
# Usage:
#   bash bin/setup-vault.sh

set -euo pipefail

VAULT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OBSIDIAN_DIR="${VAULT_ROOT}/.obsidian"

mkdir -p "$OBSIDIAN_DIR"

# app.json — общие настройки vault
cat > "${OBSIDIAN_DIR}/app.json" <<'EOF'
{
  "userIgnoreFilters": [
    ".raw/",
    "skills/",
    "hooks/",
    "bin/",
    "_templates/"
  ],
  "newFileLocation": "folder",
  "newFileFolderPath": "wiki",
  "attachmentFolderPath": "_attachments",
  "promptDelete": true
}
EOF

# appearance.json — базовые настройки внешнего вида
cat > "${OBSIDIAN_DIR}/appearance.json" <<'EOF'
{
  "theme": "obsidian",
  "baseFontSize": 16,
  "showInlineTitle": true,
  "showViewHeader": true
}
EOF

# graph.json — настройки graph view: фильтрация служебных папок
cat > "${OBSIDIAN_DIR}/graph.json" <<'EOF'
{
  "collapse-filter": false,
  "search": "-path:.raw -path:skills -path:hooks -path:bin -path:_templates",
  "showTags": false,
  "showAttachments": false,
  "hideUnresolved": false,
  "showOrphans": true,
  "collapse-color-groups": false,
  "colorGroups": [],
  "collapse-display": false,
  "showArrow": true,
  "textFadeMultiplier": 0,
  "nodeSizeMultiplier": 1,
  "lineSizeMultiplier": 1,
  "collapse-forces": false,
  "centerStrength": 0.5,
  "repelStrength": 10,
  "linkStrength": 1,
  "linkDistance": 250,
  "scale": 1,
  "close": false
}
EOF

# core-plugins.json — включённые core-плагины
cat > "${OBSIDIAN_DIR}/core-plugins.json" <<'EOF'
{
  "file-explorer": true,
  "global-search": true,
  "switcher": true,
  "graph": true,
  "backlink": true,
  "outgoing-link": true,
  "tag-pane": true,
  "properties": true,
  "page-preview": true,
  "templates": true,
  "note-composer": true,
  "command-palette": true,
  "editor-status": true,
  "bookmarks": true,
  "outline": true,
  "word-count": true,
  "file-recovery": true,
  "bases": true
}
EOF

# community-plugins.json — пустой список (пользователь устанавливает сам)
cat > "${OBSIDIAN_DIR}/community-plugins.json" <<'EOF'
[]
EOF

echo "OK: vault настроен. Открывай папку $VAULT_ROOT в Obsidian:"
echo "    Manage Vaults → Open folder as vault → выбери $VAULT_ROOT"
