#!/usr/bin/env python3
"""bench_stop_hook.py — замер § 4.4.3 «детерминированный Stop-hook».

Прогоняет каждый из четырёх скриптов Stop-hook'а трижды, берёт медиану
wall-clock времени. Для трёх скриптов с LLM-аналогом дополнительно считает
токенный размер входов и выходов — это верхняя граница того, что потребовалось
бы агенту, если бы регенерацию делал он сам.

Скрипты Stop-hook'а (из `.claude/settings.json`):
  1. bin/embed.py update       — пересчёт эмбеддингов (LLM-аналога нет:
                                  эмбеддинги — отдельная модель)
  2. bin/gen_index.py          — регенерация wiki/index.md
  3. bin/gen_dashboards.py     — регенерация wiki/meta/dashboards/*.base
  4. bin/update_dashboard.py   — регенерация wiki/meta/vault-explorer.html

Все величины — токены (tiktoken cl100k_base, погрешность ±5–10 % к Claude).

Использование:
    python3 benchmarking/bench_stop_hook.py
    python3 benchmarking/bench_stop_hook.py --out benchmarking/results/4-4-3-stop-hook.json
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

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


@dataclass
class ScriptBench:
    name: str
    command: list[str]
    runs_seconds: list[float] = field(default_factory=list)
    median_seconds: float = 0.0
    min_seconds: float = 0.0
    max_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    notes: str = ""


def _time_run(cmd: list[str]) -> float:
    t0 = time.perf_counter()
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return time.perf_counter() - t0


def time_script(name: str, command: list[str], n_runs: int = 3) -> ScriptBench:
    sb = ScriptBench(name=name, command=command)
    for _ in range(n_runs):
        sb.runs_seconds.append(_time_run(command))
    sb.median_seconds = statistics.median(sb.runs_seconds)
    sb.min_seconds = min(sb.runs_seconds)
    sb.max_seconds = max(sb.runs_seconds)
    return sb


def count_tokens(text: str) -> int:
    return len(ENC.encode(text))


def collect_frontmatter_tokens() -> tuple[int, int]:
    total = 0
    n = 0
    for p in WIKI.rglob("*.md"):
        if p.is_relative_to(META):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        m = _FRONTMATTER_RE.match(text)
        if m:
            total += count_tokens(m.group(0))
            n += 1
    return total, n


def measure_gen_index() -> ScriptBench:
    sb = time_script("gen_index.py", ["python3", "bin/gen_index.py"])
    fm_tokens, n_pages = collect_frontmatter_tokens()
    idx = WIKI / "index.md"
    out_tokens = count_tokens(idx.read_text(encoding="utf-8")) if idx.exists() else 0
    sb.input_tokens = fm_tokens
    sb.output_tokens = out_tokens
    sb.notes = f"читает frontmatter {n_pages} страниц, пишет index.md"
    return sb


def measure_gen_dashboards() -> ScriptBench:
    sb = time_script("gen_dashboards.py", ["python3", "bin/gen_dashboards.py"])
    in_tokens = 0
    for p in (WIKI / "domains").glob("*.md"):
        in_tokens += count_tokens(p.read_text(encoding="utf-8"))
    out_tokens = 0
    bases_dir = META / "dashboards"
    if bases_dir.is_dir():
        for p in bases_dir.glob("*.base"):
            out_tokens += count_tokens(p.read_text(encoding="utf-8"))
    sb.input_tokens = in_tokens
    sb.output_tokens = out_tokens
    sb.notes = "читает domain-страницы, пишет .base файлы"
    return sb


def measure_update_dashboard() -> ScriptBench:
    sb = time_script("update_dashboard.py", ["python3", "bin/update_dashboard.py"])
    in_tokens = 0
    n_pages = 0
    for p in WIKI.rglob("*.md"):
        if p.is_relative_to(META):
            continue
        try:
            in_tokens += count_tokens(p.read_text(encoding="utf-8"))
            n_pages += 1
        except (UnicodeDecodeError, FileNotFoundError):
            continue
    ve = META / "vault-explorer.html"
    out_tokens = count_tokens(ve.read_text(encoding="utf-8")) if ve.exists() else 0
    sb.input_tokens = in_tokens
    sb.output_tokens = out_tokens
    sb.notes = f"читает {n_pages} wiki-страниц + эмбеддинги, пишет vault-explorer.html"
    return sb


def measure_embed_update() -> ScriptBench:
    sb = time_script("embed.py update", ["python3", "bin/embed.py", "update"])
    sb.notes = "инкрементальный; LLM-аналога нет (отдельная embedding-модель)"
    return sb


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--out", type=Path)
    p.add_argument("--runs", type=int, default=3)
    args = p.parse_args(argv)

    print("# § 4.4.3 — детерминированный Stop-hook")
    print(f"# {args.runs} прогона на скрипт, медиана. Токены — tiktoken cl100k_base.\n")

    results = [
        measure_embed_update(),
        measure_gen_index(),
        measure_gen_dashboards(),
        measure_update_dashboard(),
    ]

    print(f"{'скрипт':<24} {'медиана':>10} {'мин':>8} {'макс':>8}  {'вход тк':>9}  {'выход тк':>9}")
    print("-" * 82)
    sum_median = 0.0
    for r in results:
        sum_median += r.median_seconds
        in_str = f"{r.input_tokens:>9,}" if r.input_tokens else "        —"
        out_str = f"{r.output_tokens:>9,}" if r.output_tokens else "        —"
        print(
            f"{r.name:<24} {r.median_seconds:>9.3f}с {r.min_seconds:>7.3f}с {r.max_seconds:>7.3f}с  "
            f"{in_str}  {out_str}"
        )
    print("-" * 82)
    print(f"{'ИТОГО':24} {sum_median:>9.3f}с")
    print()
    print("Примечания:")
    for r in results:
        if r.notes:
            print(f"  - {r.name}: {r.notes}")

    # Summary for the LLM-alternative estimate in § 4.4.3:
    # input/output token counts above represent what the agent would need to
    # read/write if it regenerated the same artefacts itself.
    llm_scripts = [r for r in results if "embed" not in r.name]
    llm_in = sum(r.input_tokens for r in llm_scripts)
    llm_out = sum(r.output_tokens for r in llm_scripts)
    print()
    print("## Токенный размер LLM-альтернативы (3 скрипта с аналогом)")
    print(f"  вход (что агент должен прочитать): {llm_in:>8,} токенов")
    print(f"  выход (что агент должен написать): {llm_out:>8,} токенов")
    print(f"  реальный конвейер: {sum_median:.2f} с/turn, 0 LLM-токенов")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "tool": "benchmarking/bench_stop_hook.py",
            "encoding": "cl100k_base",
            "runs_per_script": args.runs,
            "scripts": [asdict(r) for r in results],
            "total_median_seconds": sum_median,
            "llm_alternative": {
                "input_tokens_per_turn": llm_in,
                "output_tokens_per_turn": llm_out,
            },
        }
        args.out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nWrote: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
