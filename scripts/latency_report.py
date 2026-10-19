"""Latency and turn-quality report from logs/turns.jsonl (SPEC §1, §16; Phase 6).

Time to first audio (TTFA, caller stops speaking -> agent audio starts) as p50 / p90 / max, overall, by turn
type and by reply language, with the components (end-of-turn wait, LLM first token, TTS first byte), plus
turns without a reply, over-length replies, reply-language match and guardrail violations, each against
the §1 targets. Text evals don't write this file; console and browser calls do.

Run: `uv run python scripts/latency_report.py [--since 2026-10-16] [--room honda-] [--last 5] [--md out.md]`
"""

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

TARGET_P50, TARGET_P90 = 2.0, 3.0
STRETCH_P50 = 1.5
TARGET_OVER_LENGTH = 0.10
TARGET_LANG_MATCH = 0.95
BOOKING_TOOLS = {"check_slots", "confirm_details", "book_test_ride", "cancel_test_ride", "validate_phone",
                 "record_licence", "start_booking", "back_to_sales"}  # fmt: skip


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile (p in 0..1); None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p * len(ordered)) - 1)]


def turn_type(rec: dict) -> str:
    tools = set(rec.get("tools", []))
    if tools & BOOKING_TOOLS:
        return "booking / handoff"
    if tools:
        return "catalog tool"
    if not rec.get("llm_calls") and rec.get("ttfa_s") is not None:
        return "reply from code"
    return "LLM, no tool"


def load(path: Path, since: str | None, room: str | None, last: int | None) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if since:
        rows = [r for r in rows if r.get("at", "") >= since]
    if room:
        rows = [r for r in rows if room in str(r.get("room", ""))]
    if last:
        rooms = list(dict.fromkeys(r.get("room") for r in rows))[-last:]
        rows = [r for r in rows if r.get("room") in rooms]
    return sorted(rows, key=lambda r: (str(r.get("at", "")), str(r.get("room")), r.get("turn", 0)))


def fmt(v: float | None) -> str:
    return "–" if v is None else f"{v:.2f}"


def latency_row(name: str, rows: list[dict]) -> str:
    ttfa = [r["ttfa_s"] for r in rows if r.get("ttfa_s") is not None]
    comp = {k: percentile([r[k] for r in rows if r.get(k) is not None], 0.5)
            for k in ("eou_delay_s", "llm_ttft_s", "tts_ttfb_s")}  # fmt: skip
    return (
        f"| {name} | {len(ttfa)} | {fmt(percentile(ttfa, 0.5))} | {fmt(percentile(ttfa, 0.9))} | "
        f"{fmt(max(ttfa) if ttfa else None)} | {fmt(comp['eou_delay_s'])} | {fmt(comp['llm_ttft_s'])} | "
        f"{fmt(comp['tts_ttfb_s'])} |"
    )


def report(rows: list[dict]) -> tuple[str, bool]:
    calls = len({r.get("room") for r in rows})
    replied = [r for r in rows if r.get("ttfa_s") is not None]
    ttfa = [r["ttfa_s"] for r in replied]
    p50, p90 = percentile(ttfa, 0.5), percentile(ttfa, 0.9)
    worded = [r for r in rows if r.get("words") is not None]
    over = sum(bool(r.get("over_length")) for r in worded)
    compared = [r for r in rows if r.get("lang") and r.get("reply_lang")]
    matched = sum(r["lang"] == r["reply_lang"] for r in compared)
    violations = [v for r in rows for v in r.get("violations", [])]

    head = [
        "| Turns | n | p50 s | p90 s | max s | end of turn p50 | LLM 1st token p50 | TTS 1st audio p50 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lines = [f"# Latency report ({len(rows)} caller turns, {calls} calls)", ""]
    lines += ["## Time to first audio", "", *head]
    lines.append(latency_row("all", rows))
    by_type: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_type[turn_type(r)].append(r)
    for name in ("reply from code", "LLM, no tool", "catalog tool", "booking / handoff"):
        if by_type.get(name):
            lines.append(latency_row(name, by_type[name]))
    lines += ["", "## By reply language", "", *head]
    by_lang: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_lang[r.get("reply_lang") or "unknown"].append(r)
    for lang in sorted(by_lang):
        lines.append(latency_row(lang, by_lang[lang]))

    no_reply = len(rows) - len(replied)
    checks = [
        ("TTFA p50 ≤ 2.0 s", p50 is not None and p50 <= TARGET_P50, f"{fmt(p50)} s"
         + (" (stretch ≤ 1.5 s met)" if p50 is not None and p50 <= STRETCH_P50 else "")),
        ("TTFA p90 ≤ 3.0 s", p90 is not None and p90 <= TARGET_P90, f"{fmt(p90)} s"),
        ("Replies over ~40 words ≤ 10 %", bool(worded) and over / len(worded) <= TARGET_OVER_LENGTH,
         f"{over}/{len(worded)} = {over / len(worded):.0%}" if worded else "no data"),
        ("Reply language matches ≥ 95 %", bool(compared) and matched / len(compared) >= TARGET_LANG_MATCH,
         f"{matched}/{len(compared)} = {matched / len(compared):.0%}" if compared else "no data"),
    ]  # fmt: skip
    lines += ["", "## Against the §1 targets", "", "| Target | Result | Measured |", "|---|---|---|"]
    lines += [f"| {name} | {'met' if ok else 'NOT met'} | {measured} |" for name, ok, measured in checks]
    lines += [
        "",
        f"- Turns without an agent reply: {no_reply} of {len(rows)} (interrupted, ended calls, or failures)",
        f"- Guardrail violations: {len(violations)}"
        + (f" ({', '.join(sorted(set(violations))[:5])})" if violations else ""),
    ]
    return "\n".join(lines), all(ok for _, ok, _ in checks)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="logs/turns.jsonl")
    ap.add_argument("--since", help="only turns recorded on/after this ISO date (records from 2026-10-16 on)")
    ap.add_argument("--room", help="only rooms containing this text (e.g. honda- for browser calls)")
    ap.add_argument("--last", type=int, help="only the last N calls")
    ap.add_argument("--md", help="also write the report to this Markdown file")
    args = ap.parse_args()
    rows = load(Path(args.file), args.since, args.room, args.last)
    if not rows:
        print("no turns match")
        return 1
    text, ok = report(rows)
    print(text)
    if args.md:
        Path(args.md).write_text(text + "\n", encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
