"""Phase 1 text-mode check against the real LLM (no audio): language switching, find_bike use,
verbatim prices, reply script and length.

Run: `uv run python -m evals.phase1_text_check [--deployment NAME --model MODEL]`
(needs Azure credentials in .env).
"""

import argparse
import ast
import asyncio
import time

from dotenv import load_dotenv

load_dotenv(".env")

from livekit.agents import AgentSession  # noqa: E402
from livekit.agents import metrics as lk_metrics  # noqa: E402
from livekit.agents.voice.run_result import ChatMessageEvent, FunctionCallEvent, FunctionCallOutputEvent  # noqa: E402

from honda_voice.agent.prompts import INTRO  # noqa: E402
from honda_voice.agent.sales_agent import SalesAgent  # noqa: E402
from honda_voice.agent.text_transforms import SpeechCleaner  # noqa: E402
from honda_voice.config import build_llm, get_settings  # noqa: E402
from honda_voice.db.catalog import Catalog  # noqa: E402
from honda_voice.db.pool import close_pool, get_pool  # noqa: E402
from honda_voice.domain.catalog_view import bike_summary  # noqa: E402
from honda_voice.domain.language import script_mismatch  # noqa: E402

# (STT-detected language, caller text, expected session language after the turn)
SCRIPT = [
    ("ta-IN", "தமிழ்", "ta-IN"),  # answers the greeting's language question: expect the intro, no question
    ("ta-IN", "Activa 125 விலை என்ன?", "ta-IN"),
    ("en-IN", "ok", "ta-IN"),  # one-word answer must not switch
    ("en-IN", "What is the mileage of the Shine 100?", "en-IN"),
    ("te-IN", "Telugu lo cheppandi, Shine 100 price entha?", "te-IN"),  # explicit request
    ("hi-IN", "Bajaj Pulsar kaisi bike hai? Uska mileage kitna hai?", "hi-IN"),  # competitor: pivot to Honda
    ("kn-IN", "ನನ್ನ ಬೈಕ್ insurance renewal ಬಗ್ಗೆ ಹೇಳಿ", "kn-IN"),  # out of scope
    ("ml-IN", "CB350 H'ness ന്റെ വില എത്രയാണ്?", "ml-IN"),  # price range, unverified variants
    ("en-IN", "Is the showroom open tomorrow at 6 pm?", "en-IN"),  # hours tool
    ("en-IN", "What is the ground clearance of the Activa 125?", "en-IN"),  # one specific spec only
    # Step 10: the full database catalog.
    ("en-IN", "What is the on-road price of the Hornet 2.0 in Delhi?", "en-IN"),  # on-road, pre-fetched
    ("hi-IN", "Ek lakh ke andar koi scooter batao", "hi-IN"),  # list_bikes by segment + budget
    ("en-IN", "Compare the Unicorn and the SP160 for me", "en-IN"),  # compare (or pre-fetched pair)
    ("ta-IN", "Goldwing on-road price breakdown சொல்லுங்க", "ta-IN"),  # breakdown incl. TCS, Tamil
    ("en-IN", "How much is the activa?", "en-IN"),  # bare family name: ask which Activa
]
EXPECTED_TOOL = {
    "Is the showroom open tomorrow at 6 pm?": "check_showroom_open",
    "Ek lakh ke andar koi scooter batao": "list_bikes",
}
CONSENT_WORDS = ("record", "consent", "சம்மத", "సమ్మత", "ಒಪ್ಪಿಗೆ", "സമ്മത", "sahmati")
SPOKEN_PRICE_KEYS = ("price_min_spoken", "price_max_spoken")
RUPEE_WORDS = ("rupaye", "rupees", "ரூபாய்", "రూపాయలు", "രൂപ", "ರೂಪಾಯಿ")
HONDA_MODELS = ("Shine", "Activa", "CB350", "H'ness")
COMPETITORS = ("Pulsar", "Bajaj", "Splendor", "Hero", "TVS", "Jupiter")


def _spoken_values(obj):
    """Every *_spoken price in tool output (ex-showroom, on-road, breakdown, list and compare entries)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.endswith("_spoken") and isinstance(v, str):
                yield v
            else:
                yield from _spoken_values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _spoken_values(v)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deployment", help="Azure deployment to test (default: AZURE_OPENAI_DEPLOYMENT)")
    ap.add_argument("--model", help="underlying model name, e.g. gpt-4.1-mini")
    args = ap.parse_args()
    s = get_settings()
    if args.deployment:
        s = s.model_copy(
            update={"azure_openai_deployment": args.deployment, "llm_model": args.model or s.llm_model}
        )
    print(f"LLM: {s.azure_openai_deployment} ({s.llm_model})")
    catalog = await Catalog.load(await get_pool())
    agent = SalesAgent(
        catalog=catalog,
        initial_language=s.greeting_language,
        supported_languages=s.supported_languages,
        greet=False,
    )
    failures = 0
    llm_calls: list = []
    first_ttfts: list[float] = []
    async with AgentSession(llm=build_llm(s)) as session:
        session.on(
            "metrics_collected",
            lambda ev: (
                llm_calls.append(ev.metrics) if isinstance(ev.metrics, lk_metrics.LLMMetrics) else None
            ),
        )
        await session.start(agent)
        for detected, text, expected_lang in SCRIPT:
            agent.note_detected_language(detected)
            llm_calls.clear()
            t0 = time.perf_counter()
            result = await session.run(user_input=text)
            elapsed = time.perf_counter() - t0
            reply = " ".join(
                e.item.text_content or ""
                for e in result.events
                if isinstance(e, ChatMessageEvent) and e.item.role == "assistant"
            )
            calls = [e.item.name for e in result.events if isinstance(e, FunctionCallEvent)]
            outputs = [
                ast.literal_eval(e.item.output)
                for e in result.events
                if isinstance(e, FunctionCallOutputEvent)
            ]
            # Facts come from find_bike calls or are pre-fetched in code for models named in the turn.
            outputs += [bike_summary(catalog.models[i], agent.lang) for i in catalog.mentioned(text)]
            prices = list(_spoken_values(outputs))
            cleaner = SpeechCleaner(agent.lang)
            spoken = cleaner.feed(reply) + cleaner.flush()

            problems = []
            if agent.lang != expected_lang:
                problems.append(f"language {agent.lang} != expected {expected_lang}")
            if mismatch := script_mismatch(agent.lang, reply):
                problems.append(mismatch)
            if cleaner.stats.violations:
                problems.append(f"transform violations: {cleaner.stats.violations}")
            if any(w in reply for w in RUPEE_WORDS) and not any(p in reply for p in prices):
                problems.append(f"no tool price spoken verbatim (tool gave {prices})")
            if any(c in text for c in COMPETITORS) and not any(m in reply for m in HONDA_MODELS):
                problems.append("competitor mention: no pivot to a Honda model")
            if (tool := EXPECTED_TOOL.get(text)) and tool not in calls:
                problems.append(f"expected {tool} to be called")
            if any(w in reply.lower() for w in CONSENT_WORDS):
                problems.append("mentions recording consent")
            if text == "தமிழ்" and not any(p[:25] in reply for p in INTRO.values()):
                problems.append("first turn: intro line not given")
            if cleaner.stats.words > 40:
                problems.append(f"over length: {cleaner.stats.words} words")
            failures += bool(problems)

            print(f"\n[{'FAIL' if problems else 'ok  '}] caller ({detected}): {text}")
            ttfts = [round(m.ttft, 2) for m in llm_calls]
            first_ttfts.append(ttfts[0] if ttfts else float("nan"))
            toks = [f"{m.prompt_tokens}/{m.prompt_cached_tokens}" for m in llm_calls]
            stats = f"ttft={ttfts} prompt/cached={toks} total={elapsed:.2f}s words={cleaner.stats.words}"
            print(f"       lang={agent.lang} tools={calls or '-'} {stats}")
            print(f"       agent: {spoken}")
            for p in problems:
                print(f"       ! {p}")
    await close_pool()
    print(f"\n{len(SCRIPT) - failures}/{len(SCRIPT)} turns passed")
    ok = sorted(t for t in first_ttfts if t == t)
    print(f"first-LLM-call TTFT p50={ok[len(ok) // 2]:.2f}s max={ok[-1]:.2f}s")


if __name__ == "__main__":
    asyncio.run(main())
