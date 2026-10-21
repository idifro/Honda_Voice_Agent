"""Caller-signal accuracy on the labelled set (SPEC §20.8): evals/signals/cases.yaml, 150 turns, 6 languages.

Scoring per labelled question: choice and flag must match; a multi-choice must contain every labelled value
with at most one extra (an empty label must stay empty); a 1–5 score may be off by one; a budget amount must
fall in the same band.

Run: uv run python -m evals.signals_labelled [--provider nano|laya] [--min-accuracy 0.9]
"""

import argparse
import asyncio
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import yaml
from dotenv import load_dotenv

from honda_voice.decisions.base import Answer, SignalContext
from honda_voice.decisions.questions import BY_KEY, budget_band

CASES = Path(__file__).parent / "signals" / "cases.yaml"
LANGS = ("en", "hi", "ta", "te", "ml", "kn")


def correct(key: str, want, got: Answer | None) -> bool:
    value = got.value if got else None
    kind = BY_KEY[key].kind
    if kind == "multi":
        value = value or []
        if not want:
            return not value
        return set(want) <= set(value) and len(set(value) - set(want)) <= 1
    if kind == "score":
        return value is not None and abs(value - want) <= 1
    if kind == "amount":  # scored by the band code derives from it
        return value is not None and budget_band(value) == budget_band(want)
    return value == want


def contexts(case: dict, lang: str) -> SignalContext:
    turns = []
    for turn in case.get("context", []):
        ((role, texts),) = turn.items()
        turns.append((role, texts[lang]))
    turns.append(("caller", case["text"][lang]))
    return SignalContext(turns)


async def run(provider, cases: list[dict], concurrency: int = 8):
    sem = asyncio.Semaphore(concurrency)

    async def one(case, lang):
        async with sem:
            try:
                return case, lang, await asyncio.wait_for(provider.ask(contexts(case, lang)), 30)
            except Exception as e:
                return case, lang, e

    return await asyncio.gather(*(one(c, lang) for c in cases for lang in LANGS))


def report(results, min_accuracy: float) -> bool:
    by_lang, by_q = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
    latencies, misses, errors = [], [], []
    for case, lang, res in results:
        if isinstance(res, Exception):
            errors.append(f"{case['id']} [{lang}]: {type(res).__name__}: {res}")
            for key in case["labels"]:
                by_lang[lang][1] += 1
                by_q[key][1] += 1
            continue
        latencies.append(res.latency_ms)
        for key, want in case["labels"].items():
            ok = correct(key, want, res.answers.get(key))
            for bucket in (by_lang[lang], by_q[key]):
                bucket[0] += ok
                bucket[1] += 1
            if not ok:
                got = res.answers.get(key)
                misses.append(f"{case['id']} [{lang}] {key}: want {want}, got {got.value if got else None}")
    print(f"provider: {results[0][2].provider if not isinstance(results[0][2], Exception) else '?'}")
    print("\n| Language | Right | Accuracy |\n|---|---|---|")
    passed = True
    for lang in LANGS:
        ok, n = by_lang[lang]
        acc = ok / n if n else 0
        passed &= acc >= min_accuracy
        print(f"| {lang} | {ok}/{n} | {acc:.0%}{'' if acc >= min_accuracy else ' (below target)'} |")
    print("\n| Question | Right |\n|---|---|")
    for key, (ok, n) in sorted(by_q.items()):
        print(f"| {key} | {ok}/{n} |")
    if latencies:
        lat = sorted(latencies)
        p90 = lat[int(0.9 * (len(lat) - 1))]
        print(f"\nlatency per turn: p50 {statistics.median(lat):.0f} ms, p90 {p90} ms, max {lat[-1]} ms")
    for line in misses:
        print("  miss:", line)
    for line in errors:
        print("  error:", line)
    print(f"\n{'PASS' if passed and not errors else 'FAIL'}: target {min_accuracy:.0%} per language")
    return passed and not errors


def main() -> None:
    load_dotenv(".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["nano", "laya"], default=None, help="default: SIGNAL_PROVIDER")
    ap.add_argument("--min-accuracy", type=float, default=0.9)
    args = ap.parse_args()
    from honda_voice.agent.advisor import build_provider
    from honda_voice.config import get_settings

    settings = get_settings()
    if args.provider:
        settings = settings.model_copy(update={"signal_provider": args.provider})
    cases = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    results = asyncio.run(run(build_provider(settings), cases))
    sys.exit(0 if report(results, args.min_accuracy) else 1)


if __name__ == "__main__":
    main()
