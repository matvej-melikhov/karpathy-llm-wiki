#!/usr/bin/env python3
"""bench_skill_profile.py — замер § 4.5 «профиль типовой операции».

Запускает реальные сессии claude CLI для каждого скилла и измеряет:
  - wall-clock время выполнения
  - входные токены (из JSONL-логов сессии)
  - выходные токены
  - кэшированные токены (cache_read_input_tokens)

Для каждого скилла определён тестовый сценарий в SCENARIOS.
Скрипт запускает N прогонов на скилл, затем усредняет результаты.

Использование:
    python3 benchmarking/bench_skill_profile.py
    python3 benchmarking/bench_skill_profile.py --runs 3
    python3 benchmarking/bench_skill_profile.py --skill query
    python3 benchmarking/bench_skill_profile.py --out benchmarking/results/4-5-profile.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

PROJECT_DIR = Path.cwd().resolve()

# JSONL-логи Claude Code хранятся в ~/.claude/projects/<escaped-path>/
def _log_dir() -> Path:
    escaped = str(PROJECT_DIR).lstrip("/").replace("/", "-")
    return Path.home() / ".claude" / "projects" / escaped


# Тестовые сценарии: prompt, который будет передан claude через --print.
# Каждый сценарий должен запускать конкретный скилл одним ходом.
SCENARIOS: dict[str, str] = {
    "query": "/query Что такое механизм внимания (attention) в трансформерах?",
    "save": "/save Вопрос: зачем нужен механизм внимания? Ответ: позволяет модели взвешивать важность каждого токена относительно других при построении представлений.",
    "edge": "/edge Найди связи между механизмом внимания и архитектурой трансформера",
    "snapshot": "/snapshot",
    "ingest": "/ingest raw/test_bench.md",
}

# Для /ingest нужен тестовый источник — создадим минимальный файл если его нет.
INGEST_TEST_FILE = Path("raw/test_bench.md")
INGEST_TEST_CONTENT = """\
# Тест bench_skill_profile

Минимальный файл для замера /ingest. Содержит одно понятие:

## Бенчмаркинг агентов

Измерение производительности агентных систем через запуск воспроизводимых сценариев
и разбор логов: время, входные/выходные токены, использование кэша.
"""


@dataclass
class RunResult:
    skill: str
    elapsed_s: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    jsonl_path: str = ""
    error: str = ""


@dataclass
class SkillStats:
    skill: str
    n_runs: int
    mean_elapsed_s: float
    mean_input_tokens: float
    mean_output_tokens: float
    mean_cache_read_tokens: float
    runs: list[RunResult] = field(default_factory=list)


def _snapshot_jsonl(log_dir: Path) -> dict[Path, float]:
    """Return {path: mtime} for all JSONL files currently in log_dir."""
    if not log_dir.exists():
        return {}
    return {p: p.stat().st_mtime for p in log_dir.glob("*.jsonl")}


def _find_new_or_latest_jsonl(
    log_dir: Path, before: dict[Path, float]
) -> Path | None:
    """Return the JSONL file created or modified after `before` snapshot."""
    if not log_dir.exists():
        return None
    after = {p: p.stat().st_mtime for p in log_dir.glob("*.jsonl")}
    # Prefer files that appeared or changed
    changed = [p for p, mt in after.items() if before.get(p, 0) < mt]
    if changed:
        return max(changed, key=lambda p: p.stat().st_mtime)
    # Fallback: most recently modified
    all_files = list(after.keys())
    return max(all_files, key=lambda p: p.stat().st_mtime) if all_files else None


def _parse_usage(jsonl_path: Path) -> tuple[int, int, int, int]:
    """Parse (input, output, cache_read, cache_write) tokens from a JSONL session."""
    input_tokens = output_tokens = cache_read = cache_write = 0
    for raw in jsonl_path.read_text(encoding="utf-8").splitlines():
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        # usage может быть на верхнем уровне или внутри message
        usage = msg.get("usage") or msg.get("message", {}).get("usage") or {}
        input_tokens += usage.get("input_tokens", 0)
        output_tokens += usage.get("output_tokens", 0)
        cache_read += usage.get("cache_read_input_tokens", 0)
        cache_write += usage.get("cache_creation_input_tokens", 0)
    return input_tokens, output_tokens, cache_read, cache_write


def run_once(skill: str, prompt: str, log_dir: Path) -> RunResult:
    before = _snapshot_jsonl(log_dir)
    t0 = time.perf_counter()
    proc = subprocess.run(
        ["claude", "--print", "-p", prompt],
        capture_output=True,
        text=True,
    )
    elapsed = time.perf_counter() - t0

    if proc.returncode != 0:
        return RunResult(
            skill=skill,
            elapsed_s=elapsed,
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            error=proc.stderr[:300],
        )

    jsonl = _find_new_or_latest_jsonl(log_dir, before)
    if jsonl is None:
        return RunResult(
            skill=skill,
            elapsed_s=elapsed,
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            error="no JSONL log found",
        )

    inp, out, cr, cw = _parse_usage(jsonl)
    return RunResult(
        skill=skill,
        elapsed_s=elapsed,
        input_tokens=inp,
        output_tokens=out,
        cache_read_tokens=cr,
        cache_write_tokens=cw,
        jsonl_path=str(jsonl),
    )


def aggregate(skill: str, runs: list[RunResult]) -> SkillStats:
    ok = [r for r in runs if not r.error]
    if not ok:
        return SkillStats(
            skill=skill, n_runs=0,
            mean_elapsed_s=0, mean_input_tokens=0,
            mean_output_tokens=0, mean_cache_read_tokens=0,
            runs=runs,
        )
    return SkillStats(
        skill=skill,
        n_runs=len(ok),
        mean_elapsed_s=round(statistics.mean(r.elapsed_s for r in ok), 2),
        mean_input_tokens=round(statistics.mean(r.input_tokens for r in ok)),
        mean_output_tokens=round(statistics.mean(r.output_tokens for r in ok)),
        mean_cache_read_tokens=round(statistics.mean(r.cache_read_tokens for r in ok)),
        runs=runs,
    )


def ensure_ingest_file() -> None:
    if not INGEST_TEST_FILE.exists():
        INGEST_TEST_FILE.parent.mkdir(parents=True, exist_ok=True)
        INGEST_TEST_FILE.write_text(INGEST_TEST_CONTENT, encoding="utf-8")
        print(f"  создан тестовый файл {INGEST_TEST_FILE}")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--runs", type=int, default=5, help="Прогонов на скилл (default 5).")
    p.add_argument(
        "--skill",
        choices=list(SCENARIOS.keys()),
        help="Запустить только один скилл.",
    )
    p.add_argument("--out", type=Path)
    args = p.parse_args(argv)

    log_dir = _log_dir()
    print(f"# § 4.5 — профиль типовой операции\n")
    print(f"# Логи: {log_dir}")
    print(f"# Прогонов на скилл: {args.runs}\n")

    if not log_dir.exists():
        print(
            f"WARN: директория логов не найдена: {log_dir}\n"
            "  Убедитесь, что claude CLI запускался в этом проекте хотя бы один раз.",
            file=sys.stderr,
        )

    skills = [args.skill] if args.skill else list(SCENARIOS.keys())

    if "ingest" in skills:
        ensure_ingest_file()

    all_stats: list[SkillStats] = []

    print(f"{'скилл':<12} {'N':>3}  {'ср. время':>10}  {'ср. input':>12}  {'ср. output':>12}")
    print("-" * 60)

    for skill in skills:
        prompt = SCENARIOS[skill]
        runs: list[RunResult] = []
        for i in range(args.runs):
            print(f"  {skill} [{i+1}/{args.runs}]...", end=" ", flush=True)
            result = run_once(skill, prompt, log_dir)
            runs.append(result)
            if result.error:
                print(f"ERROR: {result.error}")
            else:
                print(f"{result.elapsed_s:.1f}s  {result.input_tokens:,}tk in  {result.output_tokens:,}tk out")

        stats = aggregate(skill, runs)
        all_stats.append(stats)
        print(
            f"  → {skill:<10} N={stats.n_runs}  "
            f"{stats.mean_elapsed_s:>8.1f}s  "
            f"{stats.mean_input_tokens:>10,}tk  "
            f"{stats.mean_output_tokens:>10,}tk"
        )
        print()

    print("-" * 60)
    print("\nТаблица (для §4.5):")
    print(f"| {'Операция':<12} | {'N':>3} | {'Ср. время, с':>13} | {'Ср. input, токены':>18} | {'Ср. output, токены':>19} |")
    print(f"|{'-'*14}|{'-'*5}|{'-'*15}|{'-'*20}|{'-'*21}|")
    for s in all_stats:
        print(
            f"| `/{s.skill}`{' '*(11-len(s.skill))} | {s.n_runs:>3} | "
            f"{s.mean_elapsed_s:>13.1f} | "
            f"{s.mean_input_tokens:>18,} | "
            f"{s.mean_output_tokens:>19,} |"
        )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "tool": "benchmarking/bench_skill_profile.py",
            "runs_per_skill": args.runs,
            "results": [asdict(s) for s in all_stats],
        }
        args.out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nWrote: {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
