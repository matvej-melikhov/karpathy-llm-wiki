#!/usr/bin/env python3
"""summarize.py — собрать все четыре результата § 4.4 в одну таблицу.

Запускает четыре отдельных замера (или подтягивает их сохранённые
результаты из benchmarking/results/) и печатает сводную таблицу в виде,
который удобно вставить в текст главы 4.

Использование:
    python3 benchmarking/summarize.py            # прочитать results/*.json
    python3 benchmarking/summarize.py --rerun    # перезапустить все замеры
    python3 benchmarking/summarize.py --md       # markdown вместо ascii
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


RESULTS = Path("benchmarking/results")
BENCH = Path("benchmarking")


def _rerun_all() -> None:
    cmds = [
        ["python3", str(BENCH / "measure_cache.py"), "--out", str(RESULTS / "4-4-1-cache.json")],
        ["python3", str(BENCH / "bench_query.py"), "--out", str(RESULTS / "4-4-2-query.json")],
        ["python3", str(BENCH / "bench_stop_hook.py"), "--out", str(RESULTS / "4-4-3-stop-hook.json")],
        ["python3", str(BENCH / "bench_lint.py"), "--out", str(RESULTS / "4-4-4-lint.json")],
    ]
    for c in cmds:
        print(f"$ {' '.join(c)}")
        subprocess.run(c, check=False)
        print()


def _load(name: str) -> dict:
    p = RESULTS / name
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def build_rows() -> list[tuple[str, str, str, str, str]]:
    """Return (section, мера, real, alternative, ratio) for each."""
    rows = []

    # ─── § 4.4.1 cache ────────────────────────────────────────────────
    c = _load("4-4-1-cache.json")
    if c:
        cache_t = c["cache_tokens"]
        fallback_t = c["fallback_total_tokens"]
        ratio = fallback_t / max(cache_t, 1)
        rows.append((
            "§ 4.4.1",
            "горячий кэш cache.md между сессиями",
            f"{cache_t:,} токенов на разогрев",
            f"{fallback_t:,} токенов без кэша (log+index+страницы)",
            f"{ratio:.1f}×",
        ))

    # ─── § 4.4.2 query ────────────────────────────────────────────────
    q = _load("4-4-2-query.json")
    if q:
        med_topk = q["median_topk_tokens"]
        baseline = q["baseline"]
        rows.append((
            "§ 4.4.2",
            "гибридный поиск (на 62 страницах)",
            f"top-{q['top_k']} ≈ {med_topk:,} токенов в контекст",
            f"index+5 стр ≈ {baseline['realistic_no_prefilter_tokens']:,} токенов",
            f"{med_topk/max(baseline['realistic_no_prefilter_tokens'],1):.2f}×",
        ))
        # Extrapolation note as a second row
        for target in (200, 500):
            idx_at = int(baseline["index_md_tokens"] * target / max(baseline["content_pages_count"], 1))
            realistic_at = idx_at + 5 * baseline["median_page_tokens"]
            ratio_at = med_topk / max(realistic_at, 1)
            rows.append((
                "",
                f"  └─ экстраполяция на {target} страниц",
                f"top-{q['top_k']} ≈ {med_topk:,}",
                f"index+5 стр ≈ {realistic_at:,}",
                f"{ratio_at:.2f}×",
            ))

    # ─── § 4.4.3 stop-hook ────────────────────────────────────────────
    s = _load("4-4-3-stop-hook.json")
    if s:
        sec = s["total_median_seconds"]
        llm = s["llm_alternative"]
        in_t = llm.get("input_tokens_per_turn", 0)
        out_t = llm.get("output_tokens_per_turn", 0)
        rows.append((
            "§ 4.4.3",
            "Stop-hook регенерация артефактов",
            f"{sec:.2f} с/turn, 0 LLM-токенов",
            f"~{in_t:,} вх + {out_t:,} вых токенов/turn",
            "∞",
        ))

    # ─── § 4.4.4 lint ─────────────────────────────────────────────────
    l = _load("4-4-4-lint.json")
    if l:
        est = l.get("layer2_cost_estimates", {})
        a = est.get("with_layer15_prefilter", {})
        b = est.get("without_layer15", {})
        speedup = est.get("speedup_factor", 0)
        rows.append((
            "§ 4.4.4",
            f"lint Layer 2 ({l.get('files_checked')} файлов, {l['totals']['layer15']} пар из L1.5)",
            f"~{a.get('input_tokens', 0):,} вх токенов",
            f"~{b.get('input_tokens', 0):,} вх токенов (полный перебор {b.get('pairs', 0):,} пар)",
            f"~{int(speedup)}×",
        ))

    return rows


def _print_ascii(rows: list[tuple[str, str, str, str, str]]) -> None:
    if not rows:
        print("Нет данных. Запусти `python3 benchmarking/summarize.py --rerun`.")
        return
    head = ("Раздел", "Замер", "Реализовано", "LLM-альтернатива", "Выигрыш")
    cols = list(zip(*([head] + rows)))
    widths = [max(len(s) for s in col) for col in cols]
    sep = "  "
    print(sep.join(s.ljust(w) for s, w in zip(head, widths)))
    print(sep.join("-" * w for w in widths))
    for r in rows:
        print(sep.join(s.ljust(w) for s, w in zip(r, widths)))


def _print_md(rows: list[tuple[str, str, str, str, str]]) -> None:
    print("| Раздел | Замер | Реализовано | LLM-альтернатива | Выигрыш |")
    print("|---|---|---|---|---|")
    for r in rows:
        print("| " + " | ".join(r) + " |")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rerun", action="store_true", help="Re-run all benchmarks before summarizing.")
    p.add_argument("--md", action="store_true", help="Output as Markdown table instead of ASCII.")
    args = p.parse_args(argv)

    if args.rerun:
        _rerun_all()

    rows = build_rows()
    print("# Сводная таблица результатов § 4.4 главы 4\n")
    if args.md:
        _print_md(rows)
    else:
        _print_ascii(rows)
    print()
    print("Цифры считаются теоретически (детерминированные замеры + tiktoken-оценки")
    print("LLM-альтернатив). Подробности и сырые данные — в benchmarking/results/.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
