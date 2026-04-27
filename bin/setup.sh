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
echo "Setup complete. Supported source formats:"
echo "  - Markdown files (.md)       — direct read"
echo "  - URLs (https://)            — defuddle"
echo "  - PDF files (.pdf)           — bin/transcribe.py (pymupdf4llm)"
echo "  - DOCX files (.docx)         — bin/transcribe.py (pandoc)"
echo "  - Images (.png/.jpg/...)     — Claude reads natively"
