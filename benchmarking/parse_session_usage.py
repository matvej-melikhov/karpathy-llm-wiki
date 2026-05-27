#!/usr/bin/env python3
"""parse_session_usage.py — извлечь usage-метрики из JSONL-логов Claude Code.

Claude Code пишет каждую сессию в ~/.claude/projects/<project-hash>/<session-id>.jsonl.
Каждая запись типа `assistant` содержит `message.usage` с полями:
    - input_tokens                  свежие входные токены (не из кэша)
    - cache_creation_input_tokens   записаны в prompt cache в первый раз
    - cache_read_input_tokens       прочитаны из prompt cache (~10× дешевле)
    - output_tokens                 сгенерированы моделью
    - iterations[]                  подсообщения tool-use (содержат те же поля)

Скрипт агрегирует эти числа по сессии и/или по отдельным «turn'ам»
(один turn = ответ агента на один пользовательский ввод; считается как
группа `assistant`-записей между двумя последовательными не-meta `user`-
записями).

Тарифы по умолчанию — публичные цены Anthropic на Claude Opus 4.7
($/M токенов): input 5, output 25, cache_write 6.25, cache_read 0.50.
Можно переопределить флагами.

Примеры использования:

    # Сводка по всей сессии
    python3 benchmarking/parse_session_usage.py session.jsonl

    # Разбивка по каждому turn'у
    python3 benchmarking/parse_session_usage.py session.jsonl --by-turn

    # Только последний turn
    python3 benchmarking/parse_session_usage.py session.jsonl --turn -1

    # Машинно-читаемый JSON-вывод
    python3 benchmarking/parse_session_usage.py session.jsonl --json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path


# Anthropic price catalog ($/1M tokens) for Claude Opus 4.7 as of 2026-05.
# These are defaults; override via CLI flags.
DEFAULT_PRICES = {
    "input": 5.0,
    "output": 25.0,
    "cache_write": 6.25,   # cache_creation_input_tokens
    "cache_read": 0.50,     # cache_read_input_tokens
}


@dataclass
class Usage:
    input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens
        self.output_tokens += other.output_tokens

    @property
    def total_input(self) -> int:
        return (
            self.input_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )

    def cost(self, prices: dict[str, float]) -> float:
        return (
            self.input_tokens * prices["input"]
            + self.output_tokens * prices["output"]
            + self.cache_creation_input_tokens * prices["cache_write"]
            + self.cache_read_input_tokens * prices["cache_read"]
        ) / 1_000_000.0


@dataclass
class Turn:
    index: int
    user_prompt_preview: str
    assistant_messages: int = 0
    tool_use_iterations: int = 0
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    started_at: str = ""
    ended_at: str = ""


def _extract_usage(message_usage: dict) -> Usage:
    """Pull a Usage dict out of an `assistant.message.usage` object.

    The outer usage block already sums over `iterations[]`, so we can read it
    directly. We re-derive from iterations only as a sanity check.
    """
    return Usage(
        input_tokens=int(message_usage.get("input_tokens", 0)),
        cache_creation_input_tokens=int(message_usage.get("cache_creation_input_tokens", 0)),
        cache_read_input_tokens=int(message_usage.get("cache_read_input_tokens", 0)),
        output_tokens=int(message_usage.get("output_tokens", 0)),
    )


def _is_tool_result_only(message: dict) -> bool:
    """User-typed records whose content is *only* tool_result blocks aren't
    new user turns — they're the system delivering tool output back to the
    assistant within the same conceptual turn."""
    content = message.get("content")
    if not isinstance(content, list):
        return False
    blocks = [b for b in content if isinstance(b, dict)]
    if not blocks:
        return False
    return all(b.get("type") == "tool_result" for b in blocks)


def _user_prompt_preview(message: dict, n: int = 80) -> str:
    content = message.get("content")
    if isinstance(content, str):
        s = content
    elif isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    parts.append("[tool_result]")
        s = " ".join(parts)
    else:
        s = ""
    s = " ".join(s.split())
    return s[:n] + ("…" if len(s) > n else "")


def parse_session(path: Path) -> list[Turn]:
    """Walk a Claude Code session JSONL and group records into turns.

    A new turn starts at every non-meta `user` record. `system` and other
    record types are skipped. `assistant` records contribute to the most
    recent turn.
    """
    turns: list[Turn] = []
    current: Turn | None = None

    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            rtype = rec.get("type")
            ts = rec.get("timestamp", "") or ""

            if rtype == "user" and not rec.get("isMeta"):
                msg = rec.get("message", {}) or {}
                # tool_result-only "user" records are part of the surrounding
                # assistant turn, not a new one.
                if _is_tool_result_only(msg):
                    continue
                current = Turn(
                    index=len(turns),
                    user_prompt_preview=_user_prompt_preview(msg),
                    started_at=ts,
                )
                turns.append(current)
                continue

            if rtype == "assistant" and current is not None:
                msg = rec.get("message", {}) or {}
                current.assistant_messages += 1
                current.tool_use_iterations += len(msg.get("iterations") or [])
                if not current.model:
                    current.model = msg.get("model", "")
                current.usage.add(_extract_usage(msg.get("usage", {}) or {}))
                if ts:
                    current.ended_at = ts

    return turns


def _select_turns(turns: list[Turn], args: argparse.Namespace) -> list[Turn]:
    if args.turn is not None:
        idx = args.turn if args.turn >= 0 else len(turns) + args.turn
        if 0 <= idx < len(turns):
            return [turns[idx]]
        return []
    if args.turn_range is not None:
        lo, hi = args.turn_range
        lo = lo if lo >= 0 else len(turns) + lo
        hi = hi if hi >= 0 else len(turns) + hi
        return turns[lo : hi + 1]
    if args.since is not None:
        return [t for t in turns if t.started_at >= args.since]
    return turns


def _format_int(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _print_table(turns: list[Turn], prices: dict[str, float]) -> None:
    print(f"{'#':>3} {'time':<19} {'in':>10} {'out':>8} {'cache_r':>10} {'cache_w':>10}  {'$':>7}  prompt")
    print("-" * 110)
    total = Usage()
    for t in turns:
        total.add(t.usage)
        print(
            f"{t.index:>3} {t.started_at[:19]:<19} "
            f"{_format_int(t.usage.input_tokens):>10} "
            f"{_format_int(t.usage.output_tokens):>8} "
            f"{_format_int(t.usage.cache_read_input_tokens):>10} "
            f"{_format_int(t.usage.cache_creation_input_tokens):>10}  "
            f"${t.usage.cost(prices):>6.4f}  {t.user_prompt_preview}"
        )
    print("-" * 110)
    print(
        f"{'TOT':>3} {'':<19} "
        f"{_format_int(total.input_tokens):>10} "
        f"{_format_int(total.output_tokens):>8} "
        f"{_format_int(total.cache_read_input_tokens):>10} "
        f"{_format_int(total.cache_creation_input_tokens):>10}  "
        f"${total.cost(prices):>6.4f}"
    )


def _print_summary(turns: list[Turn], prices: dict[str, float], path: Path) -> None:
    total = Usage()
    for t in turns:
        total.add(t.usage)
    print(f"session: {path.name}")
    if turns:
        models = sorted({t.model for t in turns if t.model})
        print(f"model:   {', '.join(models) or 'n/a'}")
        print(f"turns:   {len(turns)} (from {turns[0].started_at[:19]} to {turns[-1].ended_at[:19]})")
    print(f"input_tokens (fresh):      {_format_int(total.input_tokens):>12}")
    print(f"cache_creation (write):    {_format_int(total.cache_creation_input_tokens):>12}")
    print(f"cache_read (replay):       {_format_int(total.cache_read_input_tokens):>12}")
    print(f"output_tokens:             {_format_int(total.output_tokens):>12}")
    print(f"total input (sum of three):{_format_int(total.total_input):>12}")
    print(f"cost @ Opus 4.7 list:      ${total.cost(prices):.4f}")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("session_jsonl", type=Path)
    p.add_argument("--by-turn", action="store_true", help="Print per-turn table instead of summary.")
    p.add_argument("--turn", type=int, help="Print only one turn (negative indices allowed).")
    p.add_argument(
        "--turn-range",
        type=lambda s: tuple(int(x) for x in s.split("-")),
        help="Print turns in inclusive range, e.g. 3-7.",
    )
    p.add_argument("--since", type=str, help="Only include turns starting at/after this ISO timestamp.")
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    p.add_argument("--price-input", type=float, default=DEFAULT_PRICES["input"])
    p.add_argument("--price-output", type=float, default=DEFAULT_PRICES["output"])
    p.add_argument("--price-cache-write", type=float, default=DEFAULT_PRICES["cache_write"])
    p.add_argument("--price-cache-read", type=float, default=DEFAULT_PRICES["cache_read"])
    args = p.parse_args(argv)

    prices = {
        "input": args.price_input,
        "output": args.price_output,
        "cache_write": args.price_cache_write,
        "cache_read": args.price_cache_read,
    }

    if not args.session_jsonl.exists():
        print(f"file not found: {args.session_jsonl}", file=sys.stderr)
        return 2

    turns = parse_session(args.session_jsonl)
    turns = _select_turns(turns, args)

    if args.json:
        out = {
            "file": str(args.session_jsonl),
            "prices_per_million": prices,
            "turns": [
                {
                    **asdict(t),
                    "usage": asdict(t.usage),
                    "cost": t.usage.cost(prices),
                }
                for t in turns
            ],
        }
        # Summed totals as well, for convenience.
        total = Usage()
        for t in turns:
            total.add(t.usage)
        out["total"] = {**asdict(total), "cost": total.cost(prices)}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if args.by_turn:
        _print_table(turns, prices)
    else:
        _print_summary(turns, prices, args.session_jsonl)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
