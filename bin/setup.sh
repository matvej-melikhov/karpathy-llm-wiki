#!/usr/bin/env bash
# Install all external dependencies for llm-wiki.
#
# Tested on macOS with Homebrew. For Linux, replace `brew install X` with your
# package manager (`apt install X`, `dnf install X`, etc.).
#
# Run from project root:  bash bin/setup.sh

set -e

echo "==> Checking Node.js / npm..."
if ! command -v npm >/dev/null 2>&1; then
  echo "npm not found. Install Node.js first: https://nodejs.org or 'brew install node'"
  exit 1
fi

echo "==> Installing defuddle (URL ingestion)..."
npm install -g defuddle

echo "==> Installing pandoc (DOCX transcription)..."
if command -v brew >/dev/null 2>&1; then
  brew install pandoc
elif command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update && sudo apt-get install -y pandoc
else
  echo "Install pandoc manually for your OS: https://pandoc.org/installing.html"
fi

echo "==> Installing whisper-cpp + ffmpeg (audio/video transcription)..."
if command -v brew >/dev/null 2>&1; then
  brew install whisper-cpp ffmpeg
elif command -v apt-get >/dev/null 2>&1; then
  sudo apt-get install -y ffmpeg
  echo "  note: install whisper.cpp manually — https://github.com/ggerganov/whisper.cpp"
else
  echo "Install whisper-cpp and ffmpeg manually for your OS"
fi

echo "  note: download a whisper model and set \$WHISPER_MODEL, e.g.:"
echo "    mkdir -p ~/models && curl -L -o ~/models/ggml-base.bin \\"
echo "      https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin"
echo "    export WHISPER_MODEL=~/models/ggml-base.bin   # add to ~/.zshrc"

echo "==> Installing Python dependencies (PDF transcription)..."
if command -v pip3 >/dev/null 2>&1; then
  pip3 install --user --break-system-packages -r bin/requirements.txt
else
  echo "pip3 not found. Install Python 3 first."
  exit 1
fi

echo ""
echo "==> Verification:"
defuddle --version | sed 's/^/  defuddle: /'
pandoc --version | head -1 | sed 's/^/  /'
python3 -c "import pymupdf4llm; print('  pymupdf4llm: OK')"

echo ""
echo "==> Embedding service (optional, for lint --approx):"
echo "  bin/embed.py supports two providers — choose ONE:"
echo ""
echo "  Option A: Ollama (default)"
echo "    brew install ollama && ollama serve &"
echo "    ollama pull <model>          # e.g. nomic-embed-text, frida"
echo "    export EMBED_PROVIDER=ollama"
echo "    export EMBED_MODEL=<model>"
echo ""
echo "  Option B: LMStudio (or any OpenAI-compatible server)"
echo "    Download LMStudio, load embedding model, start local server."
echo "    export EMBED_PROVIDER=openai"
echo "    export EMBED_HOST=http://localhost:1234/v1"
echo "    export EMBED_MODEL=<model>"
echo ""
echo "  Then: python3 bin/embed.py update"
echo ""
echo "Setup complete. Supported source formats:"
echo "  - Markdown files (.md)       — direct read"
echo "  - URLs (https://)            — defuddle"
echo "  - PDF files (.pdf)           — bin/transcribe.py (pymupdf4llm)"
echo "  - DOCX files (.docx)         — bin/transcribe.py (pandoc)"
echo "  - Audio (.mp3/.wav/.m4a/...) — bin/transcribe.py (whisper-cpp)"
echo "  - Video (.mp4/.mov/...)      — bin/transcribe.py (ffmpeg + whisper-cpp)"
echo "  - Images (.png/.jpg/...)     — Claude reads natively"
