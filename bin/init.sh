#!/usr/bin/env bash
# llm-wiki setup wizard — интерактивная инициализация проекта.
#
# Что делает:
#   1. Ставит Python-зависимости (обязательно)
#   2. Опционально: pandoc, whisper-cpp, ffmpeg, defuddle/Node
#   3. Конфигурирует embedding-провайдер (Ollama / OpenAI-compatible / OpenRouter)
#   4. Настраивает Obsidian (.obsidian/) и убирает .gitkeep-заглушки
#   5. Переинициализирует git под пользователя (новый remote)
#
# Каждое действие печатается перед выполнением — никакой магии.
#
# Запуск:  bash bin/init.sh

set -e

# ── Colors ─────────────────────────────────────────────────────────────────
CYAN='\033[1;36m'; GREEN='\033[1;32m'; YELLOW='\033[1;33m'
RED='\033[1;31m'; DIM='\033[2m'; BOLD='\033[1m'; R='\033[0m'

# ── Helpers ────────────────────────────────────────────────────────────────
step()   { printf "\n${CYAN}${BOLD}[%s] %s${R}\n" "$1" "$2"; }
act()    { printf "${DIM}  → %s${R}\n" "$1"; eval "$1"; }
info()   { printf "  %s\n" "$1"; }
warn()   { printf "${YELLOW}  ⚠  %s${R}\n" "$1"; }
ok()     { printf "${GREEN}  ✓ %s${R}\n" "$1"; }
err()    { printf "${RED}  ✗ %s${R}\n" "$1" >&2; }

# ask_yn "question" "default(Y|N)" — returns 0 for yes, 1 for no
ask_yn() {
  local q="$1" def="${2:-Y}" prompt ans
  [[ "$def" == "Y" ]] && prompt="[Y/n]" || prompt="[y/N]"
  read -r -p "  ? $q $prompt " ans
  ans="${ans:-$def}"
  [[ "$ans" =~ ^[Yy]$ ]]
}

# ask_choice "question" "default" "opt1" "opt2" ... — echoes chosen number
ask_choice() {
  local q="$1" def="$2"; shift 2
  local i=1
  for o in "$@"; do printf "  [%d] %s\n" "$i" "$o"; i=$((i+1)); done
  local ans
  read -r -p "  Выбор [$def]: " ans
  echo "${ans:-$def}"
}

# ask_text "question" "default" — echoes user input or default
ask_text() {
  local q="$1" def="$2" ans
  read -r -p "  $q [$def]: " ans
  echo "${ans:-$def}"
}

# ── Banner ─────────────────────────────────────────────────────────────────
printf "${BOLD}\n"
echo "  llm-wiki — setup wizard"
echo "  ───────────────────────"
printf "${R}\n"
echo "  Скрипт настроит проект для конечного пользователя."
echo "  Каждое действие будет показано перед выполнением."
echo ""

# ────────────────────────────────────────────────────────────────────────────
step "1/6" "Python-зависимости (обязательно)"

if ! command -v pip3 >/dev/null 2>&1; then
  err "pip3 не найден. Установи Python 3.11+: https://python.org"
  exit 1
fi
ok "Python найден: $(python3 --version)"
act "pip3 install --user --break-system-packages -r bin/requirements.txt >/dev/null"
ok "pymupdf4llm, umap-learn, networkx, tiktoken, pytest установлены"

# ────────────────────────────────────────────────────────────────────────────
step "2/6" "Дополнительные системные зависимости (опционально)"

# Detect package manager
if command -v brew >/dev/null 2>&1; then PM="brew"
elif command -v apt-get >/dev/null 2>&1; then PM="apt"
else PM="manual"; warn "Не найден brew/apt — пакеты придётся ставить вручную"; fi

install_pkg() {
  local pkg="$1"
  case "$PM" in
    brew) act "brew install $pkg" ;;
    apt)  act "sudo apt-get install -y $pkg" ;;
    *)    warn "Поставь $pkg вручную для своей ОС" ;;
  esac
}

if command -v pandoc >/dev/null 2>&1; then
  ok "pandoc уже установлен"
elif ask_yn "Установить pandoc (нужен для /transcribe DOCX)?" "Y"; then
  install_pkg pandoc
fi

if command -v ffmpeg >/dev/null 2>&1; then
  ok "ffmpeg уже установлен"
elif ask_yn "Установить ffmpeg (нужен для /transcribe видео)?" "Y"; then
  install_pkg ffmpeg
fi

if command -v whisper-cpp >/dev/null 2>&1 || command -v whisper-cli >/dev/null 2>&1; then
  ok "whisper-cpp уже установлен"
elif ask_yn "Установить whisper-cpp (нужен для /transcribe аудио/видео)?" "Y"; then
  install_pkg whisper-cpp
fi

if command -v defuddle >/dev/null 2>&1; then
  ok "defuddle уже установлен"
elif command -v npm >/dev/null 2>&1; then
  if ask_yn "Установить defuddle (нужен для /ingest <URL>)?" "Y"; then
    act "npm install -g defuddle"
  fi
else
  warn "npm не найден — для /ingest <URL> установи Node.js: https://nodejs.org"
fi

# ────────────────────────────────────────────────────────────────────────────
step "3/6" "Embedding-провайдер (опционально, для /lint --approx)"

EMBED_PROVIDER=""; EMBED_MODEL=""; EMBED_HOST=""; EMBED_API_KEY=""

choice=$(ask_choice "Какой провайдер использовать?" "1" \
  "Ollama (локально, бесплатно, рекомендуется)" \
  "LMStudio / OpenAI-compatible сервер" \
  "OpenRouter (облако, платно)" \
  "Пропустить")

case "$choice" in
  1)
    EMBED_PROVIDER="ollama"
    EMBED_HOST="http://localhost:11434"
    if ! command -v ollama >/dev/null 2>&1; then
      if ask_yn "Установить Ollama?" "Y"; then
        install_pkg ollama
        warn "Запусти 'ollama serve' в отдельном терминале перед /lint --approx"
      fi
    else
      ok "Ollama уже установлен"
    fi
    EMBED_MODEL=$(ask_text "Какую модель скачать" "frida")
    if command -v ollama >/dev/null 2>&1 && ask_yn "Скачать модель $EMBED_MODEL сейчас?" "Y"; then
      act "ollama pull $EMBED_MODEL"
    fi
    ;;
  2)
    EMBED_PROVIDER="openai"
    EMBED_HOST=$(ask_text "URL сервера" "http://localhost:1234/v1")
    EMBED_MODEL=$(ask_text "Имя модели" "text-embedding-3-small")
    EMBED_API_KEY=$(ask_text "API-ключ (Enter если локальный сервер)" "")
    ;;
  3)
    EMBED_PROVIDER="openai"
    EMBED_HOST="https://openrouter.ai/api/v1"
    EMBED_MODEL=$(ask_text "Имя модели" "qwen/qwen3-embedding-8b")
    EMBED_API_KEY=$(ask_text "OpenRouter API-ключ" "")
    ;;
  4) info "Embedding пропущен — /lint --approx работать не будет" ;;
  *) warn "Неизвестный выбор, пропускаю" ;;
esac

# ────────────────────────────────────────────────────────────────────────────
step "4/6" "Транскрипция аудио (опционально)"

WHISPER_MODEL=""
if ask_yn "Скачать whisper-модель ggml-base.bin (~140MB)?" "N"; then
  mkdir -p ~/models
  act "curl -L -o ~/models/ggml-base.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin"
  WHISPER_MODEL="$HOME/models/ggml-base.bin"
fi

# Write .env
info "Записываю .env..."
cat > .env <<EOF
# llm-wiki — креды (сгенерировано init.sh)
EMBED_PROVIDER=$EMBED_PROVIDER
EMBED_MODEL=$EMBED_MODEL
EMBED_HOST=$EMBED_HOST
EMBED_API_KEY=$EMBED_API_KEY
WHISPER_MODEL=$WHISPER_MODEL
EOF
ok ".env создан"

# ────────────────────────────────────────────────────────────────────────────
step "5/6" "Obsidian-конфигурация vault"

if [[ -d .obsidian ]]; then
  if ask_yn ".obsidian/ уже существует, перезаписать?" "N"; then
    rm -rf .obsidian
  else
    info "Сохраняю существующий .obsidian/"
  fi
fi

if [[ ! -d .obsidian ]]; then
  act "mkdir -p .obsidian"
  cat > .obsidian/app.json <<'EOF'
{"userIgnoreFilters":["skills/","hooks/","bin/","_templates/"],"newFileLocation":"folder","newFileFolderPath":"wiki","attachmentFolderPath":"_attachments","promptDelete":true}
EOF
  cat > .obsidian/appearance.json <<'EOF'
{"theme":"obsidian","baseFontSize":16,"showInlineTitle":true,"showViewHeader":true}
EOF
  cat > .obsidian/graph.json <<'EOF'
{"search":"-path:raw -path:skills -path:hooks -path:bin -path:_templates","showTags":false,"showAttachments":false,"hideUnresolved":false,"showOrphans":true,"showArrow":true,"centerStrength":0.5,"repelStrength":10,"linkStrength":1,"linkDistance":250,"scale":1}
EOF
  cat > .obsidian/core-plugins.json <<'EOF'
{"file-explorer":true,"global-search":true,"switcher":true,"graph":true,"backlink":true,"outgoing-link":true,"tag-pane":true,"properties":true,"page-preview":true,"templates":true,"note-composer":true,"command-palette":true,"editor-status":true,"bookmarks":true,"outline":true,"word-count":true,"file-recovery":true,"bases":true}
EOF
  echo '[]' > .obsidian/community-plugins.json
  ok "Obsidian-конфиг создан"
fi

act "find wiki raw -name .gitkeep -delete 2>/dev/null || true"
ok ".gitkeep-заглушки удалены"

# ────────────────────────────────────────────────────────────────────────────
step "6/6" "Git — переинициализация под тебя"

warn "Сейчас .git/ указывает на репозиторий-шаблон. Push в него работать не будет."
if ask_yn "Создать свежий git-репо для твоей базы знаний (история шаблона удалится)?" "Y"; then
  act "rm -rf .git"
  act "git init -q -b main"
  act "git add -A"
  act 'git -c user.email=init@llm-wiki -c user.name=init commit -q -m "initial scaffold from llm-wiki template"'
  ok "Свежий git-репо создан с первым коммитом"

  remote=$(ask_text "URL твоего remote (Enter — пропустить, добавишь позже)" "")
  if [[ -n "$remote" ]]; then
    act "git remote add origin '$remote'"
    info "Готово к push: git push -u origin main"
  fi
else
  info "Git не тронут. Помни: origin указывает на чужой репо"
fi

# ────────────────────────────────────────────────────────────────────────────
printf "\n${GREEN}${BOLD}Готово.${R} Запускай: ${BOLD}claude${R}\n\n"
echo "Дальше: положи источник в raw/, потом /ingest <файл> в Claude Code."
echo "Полная справка по скиллам — /help в сессии."
