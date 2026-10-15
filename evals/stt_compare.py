"""Phase 1 STT check (SPEC §4, §14): recorded clips → Sarvam STT (codemix vs transcribe) → model-name
resolution against all catalog models, plus phone-number capture.

Clips and the ground-truth manifest live in evals/recordings/ (git-ignored).
Run: `uv run python -m evals.stt_compare`
"""

import asyncio
import csv
import difflib
import json
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(".env")

from sarvamai import SarvamAI  # noqa: E402

from honda_voice.config import get_settings  # noqa: E402
from honda_voice.db.catalog import Catalog  # noqa: E402
from honda_voice.db.pool import close_pool, get_pool  # noqa: E402
from honda_voice.domain.language import tokens  # noqa: E402
from honda_voice.domain.phone import normalize  # noqa: E402

REC_DIR = Path("evals/recordings")
CATALOG_DIR = Path("data/catalog")
MODES = ("codemix", "transcribe")
FUZZY_MIN = 0.8


def load_aliases() -> dict[str, list[str]]:
    out = {}
    for f in sorted(CATALOG_DIR.glob("*.json")):
        if f.name.startswith("_"):
            continue
        m = json.loads(f.read_text())
        names = [m["name"], m["name"].removeprefix("Honda "), *m.get("aliases", [])]
        out[m["id"]] = sorted({n.lower() for n in names})
    return out


def _norm(text: str) -> str:
    return " ".join(text.lower().replace("’", "'").split())


def exact_matches(text: str, aliases: dict[str, list[str]]) -> set[str]:
    """Whole-word alias match: what the live pre-fetch does."""
    t = _norm(text)
    return {
        bid for bid, names in aliases.items()
        if any(re.search(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", t) for a in names)
    }  # fmt: skip


def fuzzy_matches(text: str, aliases: dict[str, list[str]]) -> set[str]:
    """Sliding-window fuzzy alias match: a stand-in for Phase 2's trigram lookup."""
    toks = tokens(_norm(text))
    found = set()
    for bid, names in aliases.items():
        for a in names:
            n = len(a.split())
            for i in range(max(1, len(toks) - n + 1)):
                window = " ".join(toks[i : i + n])
                squashed = window.replace(" ", "")
                if (
                    max(
                        difflib.SequenceMatcher(None, window, a).ratio(),
                        difflib.SequenceMatcher(None, squashed, a.replace(" ", "")).ratio(),
                    )
                    >= FUZZY_MIN
                ):
                    found.add(bid)
                    break
    return found


def find_phone(text: str) -> str | None:
    toks = text.replace("।", " ").split()
    for size in range(1, min(16, len(toks)) + 1):
        for i in range(len(toks) - size + 1):
            if (p := normalize(" ".join(toks[i : i + size]).strip(".,?!"))) is not None:
                return p
    return None


def to_16k(src: Path, tmp: Path) -> Path:
    dst = tmp / (src.stem + ".16k.wav")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-ac", "1", "-ar", "16000", str(dst)], check=True
    )
    return dst


def main() -> None:
    manifest = REC_DIR / "manifest.csv"
    if not manifest.exists():
        sys.exit(f"missing {manifest}")
    rows = list(csv.DictReader(manifest.open(encoding="utf-8")))
    aliases = load_aliases()

    async def _load() -> Catalog:
        try:
            return await Catalog.load(await get_pool())
        finally:
            await close_pool()

    catalog = asyncio.run(_load())  # the live lookup: database aliases, longest match wins
    s = get_settings()
    client = SarvamAI(api_subscription_key=s.sarvam_api_key.get_secret_value())

    score = {m: defaultdict(lambda: [0, 0]) for m in MODES}  # mode -> bucket -> [hits, total]
    misses: dict[str, list[str]] = defaultdict(list)
    with tempfile.TemporaryDirectory() as tmpd:
        for row in rows:
            audio = to_16k(REC_DIR / row["file"], Path(tmpd))
            expected = {x for x in row["expected_models"].split("|") if x}
            print(f"\n{row['file']}  (expected: {sorted(expected) or '-'}"
                  f"{'; phone' if row['expected_phone'] else ''})")  # fmt: skip
            for mode in MODES:
                with audio.open("rb") as fh:
                    r = client.speech_to_text.transcribe(
                        file=fh, model=s.stt_model, mode=mode, language_code="unknown"
                    )
                text = r.transcript
                ex, fz = exact_matches(text, aliases), fuzzy_matches(text, aliases)
                db = set(catalog.mentioned(text))
                line = f"  [{mode:10s}|{r.language_code}] {text}"
                if expected:
                    for bucket in ("all", row["language"]):
                        for kind, got in (("exact", ex), ("exact+fuzzy", ex | fz)):
                            score[mode][(bucket, kind)][0] += expected <= got
                            score[mode][(bucket, kind)][1] += 1
                        # Stricter: the live database lookup must find exactly the expected models, no extras.
                        score[mode][(bucket, "db-exact-set")][0] += db == expected
                        score[mode][(bucket, "db-exact-set")][1] += 1
                    line += f"\n      exact={sorted(ex) or '-'} fuzzy={sorted(fz - ex) or '-'}"
                    line += f" db={sorted(db) or '-'}"
                    line += "  ✓" if expected <= (ex | fz) else "  ✗ MISS"
                    if not expected <= ex:
                        misses[mode].append(text)
                if row["expected_phone"]:
                    ok = find_phone(text) == row["expected_phone"]
                    score[mode][("phone", "exact")][0] += ok
                    score[mode][("phone", "exact")][1] += 1
                    line += f"\n      phone {'✓ exact' if ok else '✗ wrong/missing'}"
                print(line)

    print("\n=== Model-name resolution (clips where the full expected set was found)")
    for mode in MODES:
        parts = [f"{b}/{k}: {h}/{t} ({100 * h / t:.0f}%)" for (b, k), (h, t) in sorted(score[mode].items())]
        print(f"{mode:10s} " + " | ".join(parts))
    print("\nTranscripts not resolved by exact alias match (alias candidates for Phase 2):")
    for mode in MODES:
        for t in misses[mode]:
            print(f"  {mode}: {t}")


if __name__ == "__main__":
    main()
