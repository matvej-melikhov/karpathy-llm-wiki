#!/usr/bin/env python3
"""measure_cache.py — замер § 4.4.1 «горячий кэш контекста».

Считает размер wiki/cache.md в токенах и сравнивает с гипотетическим
сценарием восстановления контекста без кэша:
  log.md → index.md → N релевантных страниц по медианному размеру.

Все величины — токены (tiktoken cl100k_base, погрешность ±5–10 % к Claude).

Использование:
    python3 benchmarking/measure_cache.py
    python3 benchmarking/measure_cache.py --json
    python3 benchmarking/measure_cache.py --out benchmarking/results/4-4-1-cache.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

import tiktoken


CACHE_PATH = Path("wiki/cache.md")
LOG_PATH = Path("wiki/log.md")
INDEX_PATH = Path("wiki/index.md")
WIKI = Path("wiki")
META = WIKI / "meta"

# Среднее число содержательных wiki-страниц, читаемых агентом на один
# запрос без embedding-предфильтра (= K из bench_query.py, § 4.4.2).
# Используется как априорная оценка страниц warmup: без cache.md агент
# восстанавливает контекст аналогично свежему /query — читает те же K страниц.
FALLBACK_PAGES = 5


@dataclass
class CacheMeasurement:
    cache_path: str
    cache_chars: int
    cache_tokens: int
    encoding: str
    fallback_log_tokens: int
    fallback_index_tokens: int
    fallback_pages_tokens: int
    fallback_total_tokens: int
    mean_page_tokens: int
    fallback_pages: int
    tokens_saved_per_session: int
    ratio_fallback_to_cache: float


def _count(path: Path, enc) -> int:
    if not path.exists():
        return 0
    return len(enc.encode(path.read_text(encoding="utf-8")))


def _mean_page_tokens(enc) -> int:
    sizes = []
    for d in ("ideas", "entities", "questions", "domains", "minds"):
        for p in (WIKI / d).glob("*.md"):
            try:
                sizes.append(len(enc.encode(p.read_text(encoding="utf-8"))))
            except (UnicodeDecodeError, FileNotFoundError):
                continue
    if not sizes:
        return 1000
    return round(sum(sizes) / len(sizes))


def measure(cache_path: Path) -> CacheMeasurement:
    enc = tiktoken.get_encoding("cl100k_base")
    text = cache_path.read_text(encoding="utf-8")
    cache_tokens = len(enc.encode(text))

    log_tokens = _count(LOG_PATH, enc)
    index_tokens = _count(INDEX_PATH, enc)
    mean_page = _mean_page_tokens(enc)
    pages_tokens = FALLBACK_PAGES * mean_page
    fallback_total = log_tokens + index_tokens + pages_tokens
    tokens_saved = fallback_total - cache_tokens
    ratio = fallback_total / max(cache_tokens, 1)

    return CacheMeasurement(
        cache_path=str(cache_path),
        cache_chars=len(text),
        cache_tokens=cache_tokens,
        encoding="cl100k_base",
        fallback_log_tokens=log_tokens,
        fallback_index_tokens=index_tokens,
        fallback_pages_tokens=pages_tokens,
        fallback_total_tokens=fallback_total,
        mean_page_tokens=mean_page,
        fallback_pages=FALLBACK_PAGES,
        tokens_saved_per_session=tokens_saved,
        ratio_fallback_to_cache=round(ratio, 2),
    )


def _print_human(m: CacheMeasurement) -> None:
    print("# § 4.4.1 — горячий кэш контекста между сессиями")
    print()
    print(f"cache.md:          {m.cache_path}")
    print(f"размер:            {m.cache_chars:,} символов  =  {m.cache_tokens:,} токенов ({m.encoding})")
    print()
    print("## Гипотетический fallback (без cache.md)")
    print(f"  log.md:                              {m.fallback_log_tokens:>8,} токенов")
    print(f"  index.md:                            {m.fallback_index_tokens:>8,} токенов")
    print(f"  {m.fallback_pages} страниц × среднее {m.mean_page_tokens:,} токенов:   {m.fallback_pages_tokens:>8,} токенов")
    print(f"  ИТОГО fallback:                      {m.fallback_total_tokens:>8,} токенов")
    print()
    print("## Сравнение")
    print(f"  cache.md:                            {m.cache_tokens:>8,} токенов")
    print(f"  fallback:                            {m.fallback_total_tokens:>8,} токенов")
    print(f"  экономия на сессию:                  {m.tokens_saved_per_session:>8,} токенов")
    print(f"  отношение fallback / cache:          {m.ratio_fallback_to_cache:>8.1f}×")
    print()
    print("Примечание: cl100k_base — приближение к токенайзеру Claude (±5–10%).")
    print(f"FALLBACK_PAGES = {m.fallback_pages}; среднее по странице = {m.mean_page_tokens:,} токенов.")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--json", action="store_true", help="Output machine-readable JSON.")
    p.add_argument("--out", type=Path, help="Also write JSON result to this file.")
    args = p.parse_args(argv)

    if not CACHE_PATH.exists():
        print(f"cache file not found: {CACHE_PATH}", file=sys.stderr)
        return 2

    result = measure(CACHE_PATH)

    if args.json:
        print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
    else:
        _print_human(result)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(asdict(result), indent=2, ensure_ascii=False), encoding="utf-8"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
