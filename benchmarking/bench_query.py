#!/usr/bin/env python3
"""bench_query.py — замер § 4.4.2 «гибридный поиск с векторной предфильтрацией».

Для каждого тестового запроса из benchmarking/queries.txt:
  1. Запускает `bin/embed.py query -k K` и собирает top-K страниц.
  2. Замеряет wall-clock время предфильтра.
  3. Считает суммарный размер top-K страниц в токенах (объём в контексте агента
     при гибридном режиме).

Сравниваемая альтернатива (без предфильтра):
  чтение wiki/index.md + K релевантных страниц по медианному размеру.
  Без эмбеддингового поиска агент вынужден сначала прочитать мастер-индекс,
  чтобы понять, какие страницы открыть.

Все величины — токены (tiktoken cl100k_base, погрешность ±5–10 % к Claude).

Использование:
    python3 benchmarking/bench_query.py
    python3 benchmarking/bench_query.py --k 5 --out benchmarking/results/4-4-2-query.json
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

import tiktoken


ENC = tiktoken.get_encoding("cl100k_base")
WIKI = Path("wiki")
META = WIKI / "meta"
QUERIES_FILE = Path("benchmarking/queries.txt")

_RESULT_LINE = re.compile(r"^\s*[+-]?\d+\.\d+\s+(\S+)\s+—\s*(.*)$")


def count_tokens(text: str) -> int:
    return len(ENC.encode(text))


def load_queries(path: Path) -> list[str]:
    queries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        queries.append(s)
    return queries


def run_embed_query(query: str, k: int) -> tuple[list[str], float]:
    t0 = time.perf_counter()
    proc = subprocess.run(
        ["python3", "bin/embed.py", "query", "-k", str(k), query],
        capture_output=True,
        text=True,
    )
    dt = time.perf_counter() - t0
    paths: list[str] = []
    for line in proc.stdout.splitlines():
        m = _RESULT_LINE.match(line)
        if m:
            paths.append(m.group(1))
    return paths, dt


def measure_top_k_size(paths: list[str]) -> tuple[int, int]:
    total = 0
    found = 0
    for rel in paths:
        p = Path(rel)
        if p.is_file():
            total += count_tokens(p.read_text(encoding="utf-8"))
            found += 1
    return total, found


def measure_wiki_baseline(k: int) -> dict:
    """Baseline stats for the no-prefilter scenario.

    Without the embedding prefilter the agent reads:
      (a) wiki/index.md  — to identify relevant pages;
      (b) K content pages — same number as top-K, by mean size.

    This is the fair apples-to-apples comparison: same number of pages,
    but with the overhead of reading index.md first.
    """
    out: dict = {}
    idx = WIKI / "index.md"
    out["index_md_tokens"] = count_tokens(idx.read_text(encoding="utf-8")) if idx.exists() else 0

    sizes = []
    for p in WIKI.rglob("*.md"):
        if p.is_relative_to(META):
            continue
        if p.name in {"index.md", "log.md", "cache.md", "summary.md"}:
            continue
        try:
            sizes.append(count_tokens(p.read_text(encoding="utf-8")))
        except (UnicodeDecodeError, FileNotFoundError):
            continue
    out["content_pages_count"] = len(sizes)
    out["mean_page_tokens"] = round(sum(sizes) / len(sizes)) if sizes else 0
    out["all_content_pages_tokens"] = sum(sizes)

    # Realistic alternative: index.md + K pages at mean size
    out["fallback_pages"] = k
    out["fallback_tokens"] = out["index_md_tokens"] + k * out["mean_page_tokens"]
    return out


@dataclass
class QueryResult:
    query: str
    top_k: int
    paths: list[str] = field(default_factory=list)
    paths_found: int = 0
    prefilter_seconds: float = 0.0
    topk_tokens: int = 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--k", type=int, default=5,
        help="Top-K pages to retrieve (default 5).",
    )
    p.add_argument("--queries", type=Path, default=QUERIES_FILE)
    p.add_argument("--out", type=Path)
    args = p.parse_args(argv)

    queries = load_queries(args.queries)
    if not queries:
        print(f"no queries in {args.queries}", file=sys.stderr)
        return 2

    print(f"# § 4.4.2 — гибридный поиск (K={args.k}, {len(queries)} запросов)\n")
    print("# Токены: tiktoken cl100k_base (приближение к Claude, ±5–10%).\n")

    full = measure_wiki_baseline(args.k)
    print("## Baseline (без предфильтра): что агент читает вместо топ-K")
    print(f"  wiki/index.md:                  {full['index_md_tokens']:>8,} токенов")
    print(f"  {full['fallback_pages']} страниц × среднее {full['mean_page_tokens']:,} токенов:   "
          f"{full['fallback_pages'] * full['mean_page_tokens']:>8,} токенов")
    print(f"  ИТОГО fallback:                 {full['fallback_tokens']:>8,} токенов")
    print(f"  (весь vault — {full['content_pages_count']} стр., верх. граница: {full['all_content_pages_tokens']:,} токенов)")
    print()

    results: list[QueryResult] = []
    times: list[float] = []
    sizes: list[int] = []

    print("## По запросам")
    print(f"{'запрос':<55} {'время':>7}  {'топ-K токены':>13}")
    print("-" * 80)
    for q in queries:
        paths, dt = run_embed_query(q, args.k)
        size, found = measure_top_k_size(paths)
        results.append(QueryResult(
            query=q, top_k=args.k, paths=paths, paths_found=found,
            prefilter_seconds=dt, topk_tokens=size,
        ))
        times.append(dt)
        sizes.append(size)
        print(f"{q[:55]:<55} {dt:>6.2f}с  {size:>10,} тк")

    print("-" * 80)
    med_time = statistics.median(times)
    med_topk = int(statistics.median(sizes))
    print(f"медиана:  {med_time:.2f}с предфильтр,  {med_topk:,} токенов топ-{args.k}")
    print()

    ratio = full["fallback_tokens"] / max(med_topk, 1)
    print("## Итог (медианные значения)")
    print(f"  гибридный (топ-{args.k}):           {med_topk:>8,} токенов")
    print(f"  fallback (index.md + {args.k} стр.): {full['fallback_tokens']:>8,} токенов")
    print(f"  выигрыш hybrid / fallback:       {ratio:.2f}×")
    print()

    print("## Экстраполяция при росте vault")
    cur_n = full["content_pages_count"]
    cur_idx = full["index_md_tokens"]
    mean_page = full["mean_page_tokens"]
    for target_n in (100, 200, 500):
        if target_n <= cur_n:
            continue
        idx_at = int(cur_idx * target_n / max(cur_n, 1))
        fallback_at = idx_at + args.k * mean_page
        r = med_topk / max(fallback_at, 1)
        print(f"  vault {target_n:>4} стр.: fallback ≈ {fallback_at:>7,} токенов; "
              f"hybrid = {med_topk:,}; ratio {r:.2f}×")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "tool": "benchmarking/bench_query.py",
            "top_k": args.k,
            "queries_file": str(args.queries),
            "baseline": full,
            "per_query": [asdict(r) for r in results],
            "median_prefilter_seconds": med_time,
            "median_topk_tokens": med_topk,
        }
        args.out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nWrote: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
