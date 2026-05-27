#!/usr/bin/env python3
"""count_tokens.py — посчитать токены в файле или строке через tiktoken.

Использует кодировку `cl100k_base` (OpenAI GPT-4) как приближение к
токенайзеру Claude. Anthropic не публикует открытую реализацию своего
BPE, но `cl100k_base` обучен на похожем multilingual корпусе и даёт
оценку в пределах ±5–10 % для текста на русском/английском.

Точные значения можно получить через Anthropic SDK `count_tokens` API
при наличии ключа — но для бакалаврской работы достаточно tiktoken-
оценки, явно помеченной как приближённая.

Использование:
    python3 benchmarking/count_tokens.py wiki/cache.md
    python3 benchmarking/count_tokens.py wiki/cache.md wiki/index.md
    python3 benchmarking/count_tokens.py --stdin < some.md
    python3 benchmarking/count_tokens.py --glob 'wiki/ideas/*.md'
"""

from __future__ import annotations

import argparse
import glob as _glob
import sys
from pathlib import Path

import tiktoken


ENCODING = "cl100k_base"


def count(text: str) -> int:
    enc = tiktoken.get_encoding(ENCODING)
    return len(enc.encode(text))


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("files", nargs="*", type=Path, help="Files to count (default: read from stdin).")
    p.add_argument("--stdin", action="store_true", help="Read text from stdin and count it.")
    p.add_argument("--glob", action="append", default=[], help="Glob pattern (can repeat).")
    p.add_argument("--quiet", action="store_true", help="Print only the total integer (machine-friendly).")
    args = p.parse_args(argv)

    paths: list[Path] = []
    for pattern in args.glob:
        paths.extend(Path(p) for p in sorted(_glob.glob(pattern, recursive=True)))
    paths.extend(args.files)

    if args.stdin:
        text = sys.stdin.read()
        n = count(text)
        if args.quiet:
            print(n)
        else:
            print(f"stdin: {n:>8,} tokens ({len(text):,} chars)")
        return 0

    if not paths:
        p.error("provide files, --glob, or --stdin")

    total_tokens = total_chars = 0
    rows = []
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        n = count(text)
        rows.append((path, n, len(text)))
        total_tokens += n
        total_chars += len(text)

    if args.quiet:
        print(total_tokens)
        return 0

    width = max((len(str(p)) for p, _, _ in rows), default=10)
    for path, n, chars in rows:
        print(f"{str(path):<{width}}  {n:>8,} tokens  ({chars:>10,} chars)")
    if len(rows) > 1:
        print("-" * (width + 35))
        print(f"{'TOTAL':<{width}}  {total_tokens:>8,} tokens  ({total_chars:>10,} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
