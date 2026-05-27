#!/usr/bin/env python3
"""bench_lint.py — замер § 4.4.4 «распределение нагрузки в lint».

Прогоняет три-слойный lint и группирует найденные проблемы по слою.
Замеряет wall-clock время Layer 1 + Layer 1.5 (Layer 2 — агентский,
делается через сессию Claude Code, см. PROTOCOL.md).

Слои:
  Layer 1 (формальные, детерминированные):
    schema-bad-yaml, status-not-in-enum, lowercase-tags, raw-link-with-
    extension, dead-link, asymmetric-related, ...
  Layer 1.5 (семантические на эмбеддингах):
    similar-but-unlinked, synthesis-drift
  Layer 2 (агентские, LLM):
    contradiction, outdated-claim, missing-concept, domain-order,
    tag-casing — не закрываются этим скриптом, см. протокол.

Использование:
    python3 benchmarking/bench_lint.py
    python3 benchmarking/bench_lint.py --out benchmarking/results/4-4-4-lint.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import tiktoken


ENC = tiktoken.get_encoding("cl100k_base")
LINT_STATE = Path("wiki/meta/lint-reports/lint-state.json")
WIKI = Path("wiki")
META = WIKI / "meta"

# Per pairwise Layer-2 check: read both pages + emit a verdict.
LAYER2_OUTPUT_PER_CHECK = 200       # tokens emitted by the agent for one verdict

# Issue type → layer that produces it. Layer 2 types are listed for
# documentation but are not detected by static_lint.py.
LAYER_1_TYPES = {
    "schema-bad-yaml",
    "status-not-in-enum",
    "status-on-entity",
    "legacy-field",
    "lowercase-tags",
    "inline-tags",
    "raw-link-with-extension",
    "raw-ref-in-body",
    "empty-sources-section",
    "dead-link",
    "asymmetric-related",
    "orphan",
    "missing-frontmatter-field",
    "type-mismatch",
    "wikilink-bad-notation",
    "empty-section",
    "style-nit",
}
LAYER_15_TYPES = {
    "similar-but-unlinked",
    "synthesis-drift",
}
LAYER_2_TYPES = {
    "contradiction",
    "outdated-claim",
    "missing-concept",
    "domain-order",
    "tag-casing",
}


def time_lint(n_runs: int = 3) -> tuple[float, list[float]]:
    """Run static_lint.py --full n_runs times, return (median, list)."""
    times = []
    for _ in range(n_runs):
        t0 = time.perf_counter()
        subprocess.run(
            ["python3", "bin/static_lint.py", "--full"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        times.append(time.perf_counter() - t0)
    return statistics.median(times), times


def classify_issues(issues: list[dict]) -> dict[str, list[str]]:
    """Group issues by layer; also return unknown types."""
    by_layer = {"layer1": [], "layer15": [], "layer2": [], "unknown": []}
    for i in issues:
        t = i.get("type", "?")
        if t in LAYER_1_TYPES:
            by_layer["layer1"].append(t)
        elif t in LAYER_15_TYPES:
            by_layer["layer15"].append(t)
        elif t in LAYER_2_TYPES:
            by_layer["layer2"].append(t)
        else:
            by_layer["unknown"].append(t)
    return by_layer


def page_token_stats() -> tuple[int, int]:
    """Return (number of content pages, total tokens) — needed for Layer 2
    cost estimates."""
    n = 0
    total = 0
    for p in WIKI.rglob("*.md"):
        if p.is_relative_to(META):
            continue
        if p.name in {"index.md", "log.md", "cache.md", "summary.md"}:
            continue
        try:
            total += len(ENC.encode(p.read_text(encoding="utf-8")))
            n += 1
        except (UnicodeDecodeError, FileNotFoundError):
            continue
    return n, total


def estimate_layer2_cost(layer15_pairs: int, n_pages: int, total_tokens: int) -> dict:
    """Estimate Layer 2 cost in two scenarios:

    A. With Layer 1.5 prefilter (actual architecture):
       агент проверяет только пары-кандидаты, выбранные Layer 1.5.

    B. Without Layer 1.5 (batch, реалистичная альтернатива):
       все N страниц загружаются в контекст один раз, агент ищет
       противоречия за один проход. Это реалистичная альтернатива —
       весь vault влезает в контекстное окно Claude.
       Input = total_content_tokens.
    """
    avg_page = total_tokens / max(n_pages, 1)

    # Scenario A: only the pairs surfaced by Layer 1.5 are inspected.
    a_pairs = layer15_pairs
    a_input_tokens = int(a_pairs * 2 * avg_page)
    a_output_tokens = a_pairs * LAYER2_OUTPUT_PER_CHECK

    # Scenario B: load all pages into one context — realistic batch alternative.
    b_input_tokens = total_tokens
    b_output_tokens = LAYER2_OUTPUT_PER_CHECK * max(a_pairs, 1)

    return {
        "with_layer15_prefilter": {
            "pairs": a_pairs,
            "input_tokens": a_input_tokens,
            "output_tokens": a_output_tokens,
        },
        "without_layer15_batch": {
            "pages": n_pages,
            "input_tokens": b_input_tokens,
            "output_tokens": b_output_tokens,
        },
        "speedup_factor": b_input_tokens / max(a_input_tokens, 1),
    }


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--out", type=Path)
    args = p.parse_args(argv)

    if not Path("bin/static_lint.py").exists():
        print("bin/static_lint.py not found", file=sys.stderr)
        return 2

    print(f"# § 4.4.4 — распределение нагрузки в трёхслойном lint\n")
    print(f"# Запускаем bin/static_lint.py --full × {args.runs}; затем парсим lint-state.json.\n")

    median, runs = time_lint(args.runs)
    print(f"## Время выполнения Layer 1 + Layer 1.5")
    print(f"  медиана:  {median:.3f}s")
    print(f"  прогоны:  {[f'{x:.3f}s' for x in runs]}")
    print()

    state = json.loads(LINT_STATE.read_text(encoding="utf-8"))
    issues = state.get("open_issues", [])
    by_layer = classify_issues(issues)

    layer1_n = len(by_layer["layer1"])
    layer15_n = len(by_layer["layer15"])
    unknown_n = len(by_layer["unknown"])
    total_static = layer1_n + layer15_n + unknown_n

    print(f"## Распределение по слоям (на текущем vault'е)")
    print(f"  файлов проверено:      {state.get('files_checked', 0)}")
    print(f"  всего issues найдено:  {total_static}")
    print()
    if total_static:
        print(f"  Layer 1 (формальные):   {layer1_n:>3} ({layer1_n/total_static:.0%})")
        print(f"  Layer 1.5 (эмбеддинг):  {layer15_n:>3} ({layer15_n/total_static:.0%})")
        if unknown_n:
            print(f"  unknown type:           {unknown_n:>3}")
    print()

    print(f"## Разбивка Layer 1 по типам:")
    for t, n in Counter(by_layer["layer1"]).most_common():
        print(f"    {t:<28} {n}")
    print()
    print(f"## Разбивка Layer 1.5 по типам:")
    for t, n in Counter(by_layer["layer15"]).most_common():
        print(f"    {t:<28} {n}")
    print()
    print("## Layer 2 (агентский) — теоретическая оценка стоимости")
    print(f"  Типы проверок: {', '.join(sorted(LAYER_2_TYPES))}")
    print()
    n_pages, total_tokens = page_token_stats()
    avg_page = total_tokens / max(n_pages, 1)
    print(f"  Базовые числа: {n_pages} content-страниц, средняя {avg_page:,.0f} токенов.")
    print()

    # similar-but-unlinked pairs from Layer 1.5 ≈ pairs that Layer 2 would
    # inspect for contradictions in the actual architecture
    n_pairs_from_15 = sum(1 for t in by_layer["layer15"] if t == "similar-but-unlinked")
    if n_pairs_from_15 == 0:
        n_pairs_from_15 = layer15_n  # fallback estimate
    estimates = estimate_layer2_cost(n_pairs_from_15, n_pages, total_tokens)

    a = estimates["with_layer15_prefilter"]
    b = estimates["without_layer15_batch"]
    print(f"  A. С Layer 1.5 предфильтром ({a['pairs']} пар-кандидатов):")
    print(f"       input ≈ {a['input_tokens']:>10,} токенов")
    print(f"       output ≈ {a['output_tokens']:>10,} токенов")
    print()
    print(f"  B. Без Layer 1.5 — batch: все {b['pages']} страниц в один контекст:")
    print(f"       input ≈ {b['input_tokens']:>10,} токенов")
    print(f"       output ≈ {b['output_tokens']:>10,} токенов")
    print()
    print(f"  Выигрыш Layer 1.5 по входным токенам: ~{estimates['speedup_factor']:.0f}×")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "tool": "benchmarking/bench_lint.py",
            "runs": runs,
            "median_seconds": median,
            "files_checked": state.get("files_checked"),
            "issues_by_layer": {k: Counter(v) for k, v in by_layer.items()},
            "totals": {
                "layer1": layer1_n,
                "layer15": layer15_n,
                "unknown": unknown_n,
            },
            "layer2_types_documentation": sorted(LAYER_2_TYPES),
            "layer2_cost_estimates": estimates,
            "content_pages": n_pages,
            "total_content_tokens": total_tokens,
        }
        # Make Counters JSON-serializable
        data["issues_by_layer"] = {k: dict(v) for k, v in data["issues_by_layer"].items()}
        args.out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nWrote: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
