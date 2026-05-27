#!/usr/bin/env bash
# Запускается из .opencode/plugins/wiki-hooks.ts на каждый session.idle event.
# Регенерирует производные артефакты wiki: эмбеддинги, индекс, Bases-дашборды.
# Запускается detached (nohup), чтобы переживать завершение opencode в run-mode.
set +e
cd "$(dirname "$0")/.."
[ -f .env ] && { set -a; . ./.env; set +a; }
[ -f bin/embed.py ] && python3 bin/embed.py update >/dev/null 2>&1
[ -f bin/gen_dashboards.py ] && python3 bin/gen_dashboards.py >/dev/null 2>&1
[ -f bin/gen_index.py ] && python3 bin/gen_index.py >/dev/null 2>&1
afplay /System/Library/Sounds/Glass.aiff >/dev/null 2>&1 &
exit 0
