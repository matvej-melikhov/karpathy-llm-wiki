#!/usr/bin/env bash
# Загружает .env (если есть) и запускает opencode в корне проекта.
# Аргументы прокидываются прозрачно: bin/run-opencode.sh run "..." и т.п.
set -e
cd "$(dirname "$0")/.."
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi
exec opencode "$@"
