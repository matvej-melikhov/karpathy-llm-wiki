#!/usr/bin/env bash
# Install external dependencies for llm-wiki.
#
# Required:  Python 3.11+ (для всех скиллов и инфраструктуры)
# Optional:  pandoc        (для /transcribe DOCX)
#            whisper-cpp   (для /transcribe аудио/видео)
#            ffmpeg        (для /transcribe видео)
#            defuddle/Node (для /ingest <URL>)
#
# Tested on macOS with Homebrew. На Linux замени brew → apt/dnf/pacman.
#
# Run from project root:  bash bin/setup.sh

set -e

# ── Required: Python dependencies ──────────────────────────────────────────
echo "==> Installing Python dependencies (required)..."
if ! command -v pip3 >/dev/null 2>&1; then
  echo "ERROR: pip3 не найден. Установи Python 3.11+ — https://python.org"
  exit 1
fi
pip3 install --user --break-system-packages -r bin/requirements.txt
python3 -c "import pymupdf4llm; print('  pymupdf4llm OK')"

# ── Optional: pandoc (DOCX → markdown) ─────────────────────────────────────
echo "==> Installing pandoc (для /transcribe DOCX)..."
if command -v pandoc >/dev/null 2>&1; then
  echo "  pandoc уже установлен"
elif command -v brew >/dev/null 2>&1; then
  brew install pandoc
elif command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update && sudo apt-get install -y pandoc
else
  echo "  WARN: pandoc не установлен. Поставь вручную: https://pandoc.org/installing.html"
fi

# ── Optional: whisper-cpp + ffmpeg (audio/video → markdown) ────────────────
echo "==> Installing whisper-cpp + ffmpeg (для /transcribe аудио/видео)..."
if command -v brew >/dev/null 2>&1; then
  brew install whisper-cpp ffmpeg
elif command -v apt-get >/dev/null 2>&1; then
  sudo apt-get install -y ffmpeg
  echo "  WARN: whisper.cpp на Linux ставится вручную — https://github.com/ggerganov/whisper.cpp"
else
  echo "  WARN: whisper-cpp и ffmpeg не установлены — see https://github.com/ggerganov/whisper.cpp"
fi

echo "  скачай whisper-модель и пропиши путь в .env:"
echo "    mkdir -p ~/models && curl -L -o ~/models/ggml-base.bin \\"
echo "      https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin"
echo "    echo 'WHISPER_MODEL=~/models/ggml-base.bin' >> .env"

# ── Optional: defuddle (URL ingestion via Node) ────────────────────────────
echo "==> Installing defuddle (для /ingest <URL>)..."
if command -v npm >/dev/null 2>&1; then
  npm install -g defuddle && defuddle --version 2>/dev/null | sed 's/^/  defuddle: /' || true
else
  echo "  SKIP: npm не найден. Если нужен ingest URL — поставь Node: https://nodejs.org"
fi

echo ""
echo "==> Setup complete. Дальше:"
echo "  bash bin/setup-vault.sh   # инициализировать vault"
echo "  cp .env.example .env      # заполнить креды для embedding (см. README)"
