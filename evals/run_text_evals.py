"""Scripted text conversations against the real LLM and database (SPEC §15). No audio: each caller turn goes
through session.run(), with the STT language it would have had. Scenarios live in evals/scenarios/*.yaml.

Every turn is checked for: reply language and script, the 40-word limit (fixed tool lines excluded), prices
spoken verbatim from tool output, echoed internal tags, a booking only on a turn where the caller said yes,
and a licence recorded as yes only when the caller said so. Scenarios add their own per-turn and final checks.

Bookings use the demo showroom with room 'eval-text' and are cancelled and deleted at the end, so slot counts
are restored; leads and customers with eval phone numbers (90000xxxxx) are deleted too.

Run: `uv run python -m evals.run_text_evals [--only TEXT] [--tag TAG] [--deployment NAME --model MODEL]`
"""

import argparse
import ast
import asyncio
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv(".env")

from livekit.agents import AgentSession  # noqa: E402
from livekit.agents import metrics as lk_metrics  # noqa: E402
from livekit.agents.voice.run_result import ChatMessageEvent, FunctionCallEvent, FunctionCallOutputEvent  # noqa: E402

from honda_voice.agent.advisor import Advisor, build_provider  # noqa: E402
from honda_voice.agent.call_state import CallState  # noqa: E402
from honda_voice.agent.prompts import INTRO  # noqa: E402
from honda_voice.agent.sales_agent import SalesAgent  # noqa: E402
from honda_voice.agent.text_transforms import SpeechCleaner  # noqa: E402
from honda_voice.config import build_llm, get_settings  # noqa: E402
from honda_voice.db import bookings  # noqa: E402
from honda_voice.db.catalog import Catalog  # noqa: E402
from honda_voice.db.pool import close_pool, get_pool  # noqa: E402
from honda_voice.domain.catalog_view import bike_summary  # noqa: E402
from honda_voice.domain.competitors import competitor_pivot  # noqa: E402
from honda_voice.domain.datetime_resolve import BUSINESS_TZ, now_ist  # noqa: E402
from honda_voice.domain.language import script_mismatch  # noqa: E402

SCENARIOS = Path(__file__).parent / "scenarios"
ROOM = "eval-text"
EVAL_PHONE_PREFIX = "90000"  # every phone number in the scenarios; never a real caller's
MAX_WORDS = 40  # target (SPEC §8): 41–50 words is reported as a warning, more fails the turn
HARD_MAX_WORDS = 50
# Words that make a turn a "yes" (booking confirmation, licence). Union of all languages: the check is a
# safety net for the eval, not the agent's logic.
YES_WORDS = ("yes", "yeah", "confirm", "right", "correct", "i do", "i have", "haan", "ha ji", "ji haan",
             "ஆமா", "இருக்கு", "అవును", "ఉంది", "അതെ", "ഉണ്ട്", "ಹೌದು", "ಇದೆ")  # fmt: skip
RUPEE_WORDS = ("rupaye", "rupees", "ரூபா", "రూపాయ", "രൂപ", "ರೂಪಾಯ")
SAFETY_ERRORS = {"NOT_CONFIRMED_BY_CALLER", "NO_READ_BACK", "NO_CALLER_REPLY_SINCE_READ_BACK",
                 "DETAILS_DIFFER_FROM_READ_BACK", "SLOT_NOT_OFFERED", "PHONE_NOT_VALIDATED",
                 "LICENCE_NOT_CONFIRMED", "NAME_MISSING", "READ_BACK_NOT_LAST"}  # fmt: skip


@dataclass
class Result:
    name: str
    tags: list[str]
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # guard refusals etc.: shown, not failures
    turns: int = 0
    first_ttfts: list[float] = field(default_factory=list)


def load_scenarios() -> list[dict[str, Any]]:
    out = []
    for path in sorted(SCENARIOS.glob("*.yaml")):
        for sc in yaml.safe_load(path.read_text(encoding="utf-8")):
            sc.setdefault("tags", [path.stem])
            out.append(sc)
    return out


def spoken_values(obj: Any):
    """Every *_spoken price in tool output (ex-showroom, on-road, breakdown, list and compare entries)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.endswith("_spoken") and k != "reference_spoken" and isinstance(v, str):
                yield v
            else:
                yield from spoken_values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from spoken_values(v)


def compact_args(arguments: str | None) -> str:
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return arguments or ""
    return ", ".join(f"{k}={v!r}" for k, v in args.items() if v is not None)


# A price is spoken verbatim if the tool's string is in the reply, or its amount (with "approximately")
# followed by the rupee word with a case ending ("சுமார் 82,300 ரூபாயிலிருந்து" = "from about 82,300 rupees").
_RUPEE_STEMS = ("rupa", "rupee", "ரூபா", "రూపా", "രൂപ", "ರೂಪಾ")


def price_spoken(reply: str, price: str) -> bool:
    if price in reply:
        return True
    amount = price.rsplit(" ", 1)[0]
    return any(f"{amount} {stem}" in reply for stem in _RUPEE_STEMS)


def contains(text: str, needle: str) -> bool:
    return needle.casefold() in text.casefold()


# --- setup and hooks -------------------------------------------------------------------------------------


async def setup_booking(state: CallState, spec: dict[str, Any], vars: dict[str, str]) -> None:
    """A booking made before the call (for cancellation scenarios); its code goes into {ref}."""
    async with state.pool.acquire() as conn:
        search = await bookings.find_open_slots(
            conn, state.showroom_id, spec["model"], spec.get("day", "tomorrow"), spec["time"], state.clock()
        )
        res = await bookings.reserve(
            conn,
            search.offers[0].slot_id,
            spec["name"],
            spec["phone"],
            ROOM,
            state.lang,
            licence_confirmed=True,
        )
    assert res.ok, res
    vars["ref"] = res.reference
    vars["ref_spelled"] = " ".join(res.reference.replace("-", ""))


async def take_pending_slot(state: CallState) -> None:
    """Other callers take every free seat of the slot that was just read back."""
    slot_id = state.booking.pending.slot_id
    async with state.pool.acquire() as conn:
        free = await conn.fetchval("select capacity - booked from public.slots where id = $1", slot_id)
        for i in range(free):
            phone = f"{EVAL_PHONE_PREFIX}9{slot_id % 1000:03d}{i}"[:10]
            await bookings.reserve(conn, slot_id, f"Other {i}", phone, ROOM, "en-IN", licence_confirmed=True)


HOOKS = {"take_pending_slot": take_pending_slot}


# --- one scenario ----------------------------------------------------------------------------------------


async def run_scenario(sc: dict[str, Any], catalog: Catalog, pool, llm) -> Result:
    s = get_settings()
    res = Result(sc["name"], sc["tags"])
    lang = sc.get("start_language", "en-IN")
    state = CallState(
        catalog=catalog,
        lang=lang,
        supported=s.supported_languages,
        pool=pool,
        showroom_id=s.showroom_id,
        room=ROOM,
    )
    state.caller_turns = 0 if sc.get("first_turn") else 1  # 1: the language question was already answered
    vars: dict[str, str] = {}
    if spec := sc.get("setup", {}).get("booking"):
        await setup_booking(state, spec, vars)
    started = now_ist()
    expected_lang = lang
    called: set[str] = set()
    booked = cancelled = 0
    book_errors: set[str] = set()
    all_replies: list[str] = []
    call_facts: list = []
    llm_calls: list = []
    said_yes_so_far = False  # a licence answer may be recorded a turn late

    def on_metrics(ev) -> None:
        if isinstance(ev.metrics, lk_metrics.LLMMetrics) and not ev.metrics.cancelled:
            llm_calls.append(ev.metrics)

    advisor = None
    if sc.get("advisor"):  # real signals (nano) between turns, as in a live call (SPEC §20)
        advisor = Advisor(state, build_provider(s), mode="on")
        state.advisor = advisor

    print(f"\n=== {sc['name']}")
    async with AgentSession(llm=llm) as session:
        session.on("metrics_collected", on_metrics)
        await session.start(SalesAgent(state, greet=False))
        for turn in sc["turns"]:
            if state.booking.references:  # a booking made earlier in this call, as STT would write its code
                vars["ref_from_call"] = " ".join(state.booking.references[-1].replace("-", ""))
            try:
                text = turn["say"].format(**vars)
            except (
                KeyError
            ) as missing:  # e.g. {ref_from_call} when no booking was made: a failure, not a crash
                res.problems.append(f"turn {res.turns + 1}: placeholder {missing} unavailable")
                break
            exp = turn.get("expect", {})
            expected_lang = exp.get("language", expected_lang)
            if hook := turn.get("before"):
                await HOOKS[hook](state)
            stt_lang = state.detected = turn.get("lang", expected_lang)
            llm_calls.clear()
            t0 = time.perf_counter()
            offered_before = len(state.booking.offered)
            result = await session.run(user_input=text)
            elapsed = time.perf_counter() - t0
            if advisor is not None:  # live: the answers arrive while the reply plays, before the next turn
                items = list(session.history.items)
                user = next((it for it in reversed(items) if getattr(it, "role", None) == "user"), None)
                if user is not None:
                    advisor.on_item(user, items)
                    await advisor.settle()
            res.turns += 1
            if llm_calls:
                res.first_ttfts.append(llm_calls[0].ttft)

            calls = [e.item for e in result.events if isinstance(e, FunctionCallEvent)]
            names = [c.name for c in calls]
            outputs: list[tuple[str, dict]] = []
            by_call: dict[str, dict] = {}
            for e in result.events:
                if isinstance(e, FunctionCallOutputEvent):
                    try:
                        out = ast.literal_eval(e.item.output) if e.item.output else {}
                    except (ValueError, SyntaxError):
                        out = {}
                    out = out if isinstance(out, dict) else {}
                    outputs.append((e.item.name, out))
                    by_call[e.item.call_id] = out
            reply = " ".join(
                e.item.text_content or ""
                for e in result.events
                if isinstance(e, ChatMessageEvent) and e.item.role == "assistant"
            )
            all_replies.append(reply)
            cleaner = SpeechCleaner(state.lang)
            spoken = cleaner.feed(reply) + cleaner.flush()
            called |= set(names)

            problems: list[str] = []
            yes_turn = any(contains(text, w) for w in YES_WORDS)
            said_yes_so_far |= yes_turn
            for name, out in outputs:
                if name == "book_test_ride":
                    if out.get("booked"):
                        booked += 1
                        if not yes_turn:
                            problems.append("booked on a turn where the caller didn't say yes")
                    elif err := out.get("error"):
                        book_errors.add(err)
                        if err in SAFETY_ERRORS:
                            res.notes.append(f"guard refused book_test_ride: {err}")
                if name == "cancel_test_ride" and out.get("cancelled"):
                    cancelled += 1
            for c in calls:
                if c.name == "record_licence" and json.loads(c.arguments or "{}").get("has_valid_licence"):
                    if (
                        by_call.get(c.call_id, {}).get("recorded") and not said_yes_so_far
                    ):  # accepted, not tried
                        problems.append("licence recorded as yes without a yes from the caller")

            # Generic checks.
            if state.lang != expected_lang:
                problems.append(f"session language {state.lang}, expected {expected_lang}")
            if reply and (mismatch := script_mismatch(state.lang, reply)):
                problems.append(mismatch)
            if cleaner.stats.violations:
                problems.append(f"speech guardrail: {cleaner.stats.violations}")
            fixed = [out["say"] for _, out in outputs if isinstance(out.get("say"), str)]
            free_text = spoken
            for line in fixed:
                free_text = free_text.replace(line, "")
            words = len(free_text.split())
            if words > exp.get("max_words", HARD_MAX_WORDS):
                problems.append(f"over length: {words} words")
            elif words > MAX_WORDS:
                res.notes.append(f"turn {res.turns}: {words} words (target {MAX_WORDS})")
            facts = [out for _, out in outputs]
            facts += [bike_summary(catalog.models[i], state.lang) for i in catalog.mentioned(text)]
            if (pivot := competitor_pivot(text)[1]) and not catalog.mentioned(text):  # pre-fetched pivot
                facts.append(bike_summary(catalog.models[pivot], state.lang))
            call_facts.extend(facts)  # a price repeated from an earlier turn's tool output is still verbatim
            prices = list(spoken_values(call_facts))
            mentions_rupees = any(w in reply for w in RUPEE_WORDS)
            if mentions_rupees and not any(price_spoken(reply, p) for p in prices):
                problems.append(f"price not spoken verbatim from a tool (tool gave {prices[:4]})")

            # Scenario checks for this turn.
            if exp.get("price") and not any(price_spoken(reply, p) for p in prices):
                problems.append("expected a price from tool output")
            for t in exp.get("tools", []):
                if t not in names:
                    problems.append(f"expected tool {t}")
            if exp.get("slots_checked") and len(state.booking.offered) == offered_before:
                problems.append("expected a slot search (check_slots, or in code at the handoff)")
            if want := exp.get("time_24h"):  # the time the LLM extracted (rule 5: code resolves it)
                got = [json.loads(c.arguments or "{}").get("time_24h") for c in calls]
                if want not in got:
                    problems.append(f"expected time_24h {want}, tools got {[g for g in got if g]}")
            if (any_of := exp.get("tools_any")) and not set(any_of) & set(names):
                problems.append(f"expected one of tools {any_of}")
            for t in exp.get("no_tools", []):
                if t in names:
                    problems.append(f"tool {t} must not be called")
            if (any_of := exp.get("says_any")) and not any(contains(reply, w) for w in any_of):
                problems.append(f"reply should mention one of {any_of}")
            for w in exp.get("says_all", []):
                if not contains(reply, w):
                    problems.append(f"reply should mention {w!r}")
            for w in exp.get("says_none", []):
                if contains(reply, w):
                    problems.append(f"reply must not mention {w!r}")
            if exp.get("intro") and INTRO[state.lang][:25] not in reply:
                problems.append("intro line not given")
            if exp.get("honda_pivot") and not any(
                contains(reply, m["name"].removeprefix("Honda ")) for m in catalog.models.values()
            ):
                problems.append("no pivot to a Honda model")

            agent_name = type(session.current_agent).__name__
            tools_str = ", ".join(f"{c.name}({compact_args(c.arguments)})" for c in calls) or "-"
            print(f"[{'FAIL' if problems else 'ok  '}] caller ({stt_lang}): {text}")
            print(f"       lang={state.lang} agent={agent_name} {elapsed:.2f}s tools: {tools_str}")
            print(f"       agent: {spoken.strip()}")
            for p in problems:
                print(f"       ! {p}")
            res.problems += [f"turn {res.turns}: {p}" for p in problems]

    # Scenario-level checks.
    exp = sc.get("expect", {})
    if booked != exp.get("booked", 0):
        res.problems.append(f"booked {booked}, expected {exp.get('booked', 0)}")
    if cancelled != exp.get("cancelled", 0):
        res.problems.append(f"cancelled {cancelled}, expected {exp.get('cancelled', 0)}")
    for t in exp.get("tools", []):
        if t not in called:
            res.problems.append(f"tool {t} never called")
    for t in exp.get("no_tools", []):
        if t in called:
            res.problems.append(f"tool {t} must not be called")
    if (any_of := exp.get("said_any")) and not any(contains(r, w) for r in all_replies for w in any_of):
        res.problems.append(f"no reply in the call mentioned one of {any_of}")
    for err in exp.get("book_errors", []):
        if err not in book_errors:
            res.problems.append(f"expected book_test_ride error {err}")
    if want := exp.get("booking"):
        await check_booking_row(state, want, res)
    if "leads" in exp:
        async with pool.acquire() as conn:
            n = await conn.fetchval(
                "select count(*) from public.leads where created_at >= $1 and customer_phone like $2",
                started, EVAL_PHONE_PREFIX + "%",
            )  # fmt: skip
        if n != exp["leads"]:
            res.problems.append(f"leads saved {n}, expected {exp['leads']}")
    if "consented_customers" in exp:  # the customer list: callback consent recorded with its time and text
        async with pool.acquire() as conn:
            n = await conn.fetchval(
                "select count(*) from public.customers where phone like $1 and callback_consent "
                "and consent_at >= $2 and consent_text_version is not null",
                EVAL_PHONE_PREFIX + "%", started,
            )  # fmt: skip
        if n != exp["consented_customers"]:
            res.problems.append(f"consented customers {n}, expected {exp['consented_customers']}")
    for n in res.notes:
        print(f"       · {n}")
    for p in res.problems:
        if not p.startswith("turn "):
            print(f"       ! {p}")
    print(f"[{'FAIL' if res.problems else 'ok'}] {sc['name']}")
    return res


async def check_booking_row(state: CallState, want: dict[str, Any], res: Result) -> None:
    """The stored booking equals what the scenario's caller agreed to."""
    if not state.booking.references:
        return  # already reported as a booked-count mismatch
    async with state.pool.acquire() as conn:
        row = await conn.fetchrow(
            "select b.customer_name, b.customer_phone, b.language, b.licence_confirmed, s.bike_id, "
            "s.starts_at "
            "from public.bookings b join public.slots s on s.id = b.slot_id where b.reference = $1",
            state.booking.references[-1],
        )
    local = row["starts_at"].astimezone(BUSINESS_TZ)
    got = {
        "model": row["bike_id"],
        "time": local.strftime("%H:%M"),
        "name": row["customer_name"],
        "phone": row["customer_phone"],
        "language": row["language"],
    }
    for key, value in want.items():
        if got.get(key) != value:
            res.problems.append(f"booking {key} = {got.get(key)!r}, expected {value!r}")
    if row["licence_confirmed"] is not True:
        res.problems.append("booking stored without a confirmed licence")
    print(f"       db: {got['model']} {local:%a %H:%M} {got['name']} {got['language']}")


async def cleanup(pool, started) -> None:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "select reference, customer_phone from public.bookings "
            "where room_name = $1 and status = 'CONFIRMED'",
            ROOM,
        )
        for r in rows:
            await bookings.cancel(conn, r["reference"], r["customer_phone"])
        deleted = await conn.execute("delete from public.bookings where room_name = $1", ROOM)
        leads = await conn.execute(
            "delete from public.leads where created_at >= $1 and customer_phone like $2",
            started, EVAL_PHONE_PREFIX + "%",
        )  # fmt: skip
        # Eval customers: only rows nothing else points at (a pre-call booking may still reference one).
        customers = await conn.execute(
            "delete from public.customers c where c.phone like $1 "
            "and not exists (select 1 from public.bookings b where b.customer_id = c.id) "
            "and not exists (select 1 from public.leads l where l.customer_id = c.id)",
            EVAL_PHONE_PREFIX + "%",
        )
    print(
        f"\ncleanup: cancelled {len(rows)} bookings, {deleted.lower()}, leads {leads.lower()}, "
        f"customers {customers.lower()}"
    )


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="run scenarios whose name contains this text")
    ap.add_argument("--tag", help="run scenarios with this tag (the scenario file name, e.g. booking)")
    ap.add_argument("--deployment", help="Azure deployment to test (default: AZURE_OPENAI_DEPLOYMENT)")
    ap.add_argument("--model", help="underlying model name, e.g. gpt-4.1-mini")
    ap.add_argument(
        "--min-pass", type=float, default=1.0,
        help="exit 0 if at least this share of scenarios pass (the pre-demo gate uses 0.95: LLM variance)",
    )  # fmt: skip
    args = ap.parse_args()
    s = get_settings()
    if args.deployment:
        s = s.model_copy(
            update={"azure_openai_deployment": args.deployment, "llm_model": args.model or s.llm_model}
        )
    scenarios = [
        sc
        for sc in load_scenarios()
        if (not args.only or args.only.lower() in sc["name"].lower())
        and (not args.tag or args.tag in sc["tags"])
    ]
    print(f"LLM: {s.azure_openai_deployment} ({s.llm_model}); {len(scenarios)} scenarios")
    started = now_ist()
    pool = await get_pool()
    catalog = await Catalog.load(pool)
    llm = build_llm(s)
    results: list[Result] = []
    try:
        for sc in scenarios:
            try:
                results.append(await run_scenario(sc, catalog, pool, llm))
            except Exception as e:  # a crash fails the scenario, not the run
                r = Result(sc["name"], sc["tags"], problems=[f"crashed: {e!r}"])
                print(f"[FAIL] {sc['name']}: crashed: {e!r}")
                results.append(r)
    finally:
        await cleanup(pool, started)
        await close_pool()

    print("\n" + "=" * 100)
    by_tag: dict[str, list[bool]] = {}
    for r in results:
        for t in r.tags:
            by_tag.setdefault(t, []).append(not r.problems)
        print(f"{'FAIL' if r.problems else 'ok  '}  {r.name}")
        for p in r.problems:
            print(f"        ! {p}")
        for n in r.notes:
            if "words" in n:
                print(f"        ~ {n}")
    print("\n" + "  ".join(f"{t}: {sum(v)}/{len(v)}" for t, v in sorted(by_tag.items())))
    ttfts = sorted(t for r in results for t in r.first_ttfts)
    if ttfts:
        p90 = ttfts[int(0.9 * (len(ttfts) - 1))]
        print(f"first-LLM-call TTFT p50={statistics.median(ttfts):.2f}s p90={p90:.2f}s max={ttfts[-1]:.2f}s")
    passed = sum(not r.problems for r in results)
    print(f"\n{passed}/{len(results)} scenarios passed")
    return 0 if results and passed / len(results) >= args.min_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
