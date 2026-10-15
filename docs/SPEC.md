# SPEC — Honda 2-Wheeler Multilingual Voice Sales Agent (POC)

Lives at `docs/SPEC.md`. CLAUDE.md holds the hard rules; this document holds the design.

---

## 1. Goal and success criteria

A caller speaks Hindi/Hinglish, English, Tamil, Telugu, Malayalam or Kannada to a browser-based voice agent for one Honda showroom, and the agent replies in the caller's language. The agent answers questions about models, variants, prices, and mileage, and books a test ride that lands in Supabase and is mirrored to the showroom's Google Calendar.

The POC **passes** if it meets these on the eval set (§15) and in five live demo runs:

| Metric | Target |
|---|---|
| Time to first agent audio after caller stops speaking (p50 / p90) | ≤ 2.0 s / ≤ 3.0 s (stretch: p50 ≤ 1.5 s) |
| Model name correctly resolved from speech (30 recorded utterances: 10 Hindi/English, 5 each Tamil, Telugu, Malayalam, Kannada) | ≥ 90 % overall, ≥ 80 % per language |
| Reply language matches the caller's language (§4.1) | ≥ 95 % of turns |
| Scripted booking conversations completed end-to-end | ≥ 80 % |
| Bookings with wrong phone, date, or model persisted | 0 |
| Spoken prices that differ from catalog | 0 |
| Agent turns over 2 sentences / ~40 words | ≤ 10 % |

**Latency target revised (2026-10-15, end of Phase 1):** the original 1.2 / 2.0 s target assumed one fast English pipeline. Measured per-turn costs with six-language STT and a reasoning-capable LLM are: end-of-turn wait ~0.6 s (turn detector + endpointing), LLM time to first token ~0.8 s (`gpt-5.4-mini`; the faster `gpt-4.1-mini` spoke wrong prices), TTS first byte ~0.2 s, plus transport and playout, so a single-LLM-call turn lands at ~1.4–1.7 s. Console run 2 measured 1.95 / 2.69 s. The target is now ≤ 2.0 s p50 / ≤ 3.0 s p90, with p50 ≤ 1.5 s as the stretch goal for Phase 6.

## 2. Scope

**In scope:** one showroom; the full current Honda India lineup (33 models) with variants; six languages (Hindi/Hinglish, English, Tamil, Telugu, Malayalam, Kannada) with the reply language following the caller (§4.1); price/spec/mileage Q&A; whether the showroom is open on a given day/time; simple two-model comparison within Honda; test-ride booking and cancellation; callback lead capture as fallback; browser client with visualizer, transcript, and live bookings panel.

**Out of scope (non-goals):** PSTN/SIP telephony, Indian languages beyond the six above, *calculating* on-road prices (published Delhi on-road prices are in scope, §6), finance/EMI quotes, scraping the OEM website, multiple showrooms, an admin UI, and authentication beyond a shared demo passcode.

## 3. Architecture

```text
[ Next.js client (LiveKit agent-starter-react) ]
   visualizer · control bar · transcript · bookings panel (polls /api/bookings)
                 │ WebRTC (Opus)
                 ▼
[ LiveKit Cloud room ]  ←— agent explicitly dispatched via token (agent_name="honda-sales")
                           (Phase 10: self-hosted LiveKit server, §24)
                 │
                 ▼
[ Python worker: LiveKit AgentSession ]
   VAD: Silero
   Turn detection: LiveKit multilingual turn detector (fallback: STT-based)
   STT: Sarvam saaras:v4, codemix, language auto-detect → per-turn caller language (§4.1)
   LLM: one fast tool-calling model (chosen in Phase 1)
   Agents (handoff via tool call, no extra LLM hop per turn):
     ├─ SalesAgent   — greeting + language choice, catalog Q&A, showroom hours, comparisons, competitor pivot
     └─ BookingAgent — collects + confirms details, reserves slot, cancels
   Decision layer (Phase 7): Laya, self-hosted on the local GPU — per-turn signals run in parallel
                             (never delays audio); the booking-confirmation gate is the only serial call (§20)
   tts_node override → streaming text transforms (§9)
   TTS: Sarvam bulbul:v4-flash, speaker "aarti_hi_customer" in all six languages,
        language_code set per turn from the session language; pronunciation dictionary
                 │
                 ▼
[ Supabase Postgres ] ←— asyncpg pool (session pooler)   (Phase 11: local Postgres, §24)
   catalog (bikes, variants, aliases) · showrooms · slots · bookings · leads
   reserve_slot() / cancel_booking() Postgres functions (atomic)
                 │ after commit (async, retried)
                 ▼
[ Google Calendar ] showroom calendar shared with the service account (mirror only)
```

**Why no LangGraph / supervisor:** a supervisor adds a full LLM round trip to every turn, and a post-hoc guardrail node forces buffering, which removes streaming. LiveKit's native agent handoff gives the same separation of concerns at zero cost on turns that don't change agent. If LangGraph becomes a hard requirement later, wrap a single-node graph with LiveKit's LangChain adapter; do not add a routing hop.

## 4. Voice pipeline configuration

| Component | Setting | Notes |
|---|---|---|
| STT | `saaras:v4`, `mode=STT_MODE` (`codemix`), `language="unknown"` (auto-detect) | `codemix` keeps English words and model names in Latin script. The detected language feeds the session-language rule (§4.1). Phase 1 confirms on the 30 recordings. |
| VAD | Silero, default thresholds | Tune `min_silence_duration` only if eval shows clipping. |
| Turn detection | `livekit.agents.inference.TurnDetector` (livekit-agents 1.8.8; the old `turn_detector.multilingual` plugin is deprecated) | Prevents cut-offs at mid-sentence pauses. `v1` runs on LiveKit Cloud and **supports all six languages** (checked in step 6 with `supports_language`, 2026-10-15). If the cloud is unreachable it falls back to a local `v1-mini` model, which supports only `hi` and `en` of our six; other languages then end the turn on VAD silence. Phase 10 (self-hosted LiveKit) leaves only the local model. |
| Interruptions | enabled; `min_interruption_duration` ≈ 0.5 s | Caller barge-in stops agent audio. |
| LLM | Azure OpenAI deployment via `LLM_PROVIDER=azure` | Default: **`gpt-5.4-mini`** (or the newest *mini* tier available in your Azure region) with `reasoning_effort` at its lowest setting (`none`, or `minimal` on `gpt-5`/`gpt-5-mini`). Do **not** use full `gpt-5`/`gpt-5.4`/`gpt-5.5` or any `-pro` model in the live voice path; reasoning adds seconds of time-to-first-token. Phase 1 benchmarks the chosen mini model against one alternative (e.g. `gpt-5-mini` at `minimal`) on TTFT, tool-call accuracy and Hinglish quality. `max_tokens` ≈ 150 (reasoning tokens count against it). GPT-5-series models only accept `temperature=1`; don't set it. Deploy in the Azure region/deployment type closest to India that offers the model (check the Foundry catalog for Central/South India; Global Standard may route outside India). |
| TTS | `bulbul:v4-flash`, `speaker=TTS_SPEAKER` (`aarti_hi_customer`), `language_code` = session language, set per turn | One voice for all six languages (tested 2026-10-15; language-specific voices were no better and `kavitha_te_conversation` garbled English words). Use `dict_id` (Sarvam pronunciation dictionary) for: Activa, Shine, SP125, Hornet, Unicorn, H'ness, CB350, Honda. |
| Noise cancellation | LiveKit BVC if available on plan | Optional. |

**Azure setup (pre-Phase 1, 2026-10-15):** resource in **South India**, both deployments Global Standard (the subscription has 0 regional-Standard quota there). The content filter must use **Streaming mode = Asynchronous Filter**; the default filter buffers output and added ~300 ms to TTFT. With the async filter, a direct streaming test from India (no tools) measured TTFT median / p90 of `gpt-5.4-mini` (`reasoning_effort=none`) at 531 / 707 ms and `gpt-4.1-mini` at 718 / 808 ms. An East US 2 resource measured 1.5–2.2 s, so a resource in India is required. `gpt-4.1-mini` replaces `gpt-5-mini` as the comparison model because it doesn't reason before answering.

**Sarvam v4 (pre-Phase 1, 2026-10-15):** STT and TTS switch to `saaras:v4` and `bulbul:v4-flash` (configured by `STT_MODEL`, `TTS_MODEL` and `TTS_SPEAKER`). In a TTS→STT round-trip test on synthetic audio, `saaras:v4` + `codemix` kept English words and model names in Latin script and got the reference code exactly (`HND7K3Q9`), while `transcribe` mode wrote everything in Devanagari. Without `keyterms`, it misheard "Unicorn" as "Anicon" and "H'ness" as "HNS". With model-name `keyterms`, all names came out right, but "HND" in the reference code turned into "H'ness", so `keyterms` must not be active while a caller reads out a reference code (cancellation). `bulbul:v4-flash` time to first audio byte was 52–85 ms (`bulbul:v3`: 56–285 ms). v4-flash uses different voice names (`aarti_hi_customer`, `shubh_enhi_ads`, `ishita_enhi_customer`, …; `shubh` alone isn't valid). Voice chosen: `aarti_hi_customer`, which was the only tested voice whose "Unicorn" came back correct. Every voice mispronounced "H'ness", so it needs a pronunciation-dictionary entry, and the SDK parameter is `language_code` (sarvamai 0.1.37). Real callers' speech still needs the Phase 1 recordings.

**Console run 1 (2026-10-15, 9 turns, Tamil/English/Hindi):** time to first audio p50 **2.83 s**, p90 3.34 s (target 1.2 / 2.0 s). Components, p50: endpointing/EOU 0.82 s (4 of 9 turns hit **2.5 s**, i.e. the detector waited to its maximum), STT final transcript 0.74 s after speech end, LLM time to first token 0.88 s (0.53 s in the bare API test; the real prompt and tool schemas are larger), TTS first byte 0.20 s. Tool turns (two LLM calls) and single-call turns were close (2.92 vs 2.79 s), so the fixed per-turn costs dominate. One over-length reply (48 words), no guardrail violations. Latency tuning is the next Phase 1 task.

**Step 6b latency changes (2026-10-15):** (1) Sarvam STT `high_vad_sensitivity=true`: its default end-of-speech needs ~0.58 s of silence (18 of 24 frames), the preset ~64 ms; LiveKit's turn detector still decides turn-taking. (2) Endpointing `min_delay` 0.3 s, `max_delay` 1.5 s (streaming-detector default 2.5 s; run 1 hit the cap on 4/9 turns). (3) `preemptive_tts=true` (LLM was already preemptive). (4) LLM warm-up call while the greeting plays: the first call of a session took 1–6 s cold. (5) **Model facts pre-fetched in code:** models named in the transcript (whole-word alias match) get their `find_bike` output added to the turn, so product questions need one LLM call instead of two; `find_bike` stays for misheard names, competitors and anything else. Text-mode check: product turns ~0.9–1.3 s total (was ~1.8–2.3 s), first-call TTFT p50 0.88 s; prompt ~1.3–2.8k tokens with 1.1–1.7k cached, so shrinking the prompt would gain little.

**Console run 2 (after step 6b, 11 turns, Tamil/English):** time to first audio p50 **1.95 s**, p90 2.69 s (run 1: 2.83 / 3.34 s). STT final transcript p50 0.22 s (was 0.74 s); end-of-turn wait p50 0.58 s, but 3 of 10 turns hit the 1.5 s cap; LLM time to first token p50 0.85 s; TTS first byte 0.20 s. Single-LLM-call turns came in at 0.96–1.69 s. Run 2 also exposed a bug, fixed since: with preemptive TTS the reply could start before the language switch, so the language is now decided when the final transcript arrives.

**Step 7 measurements (2026-10-15):**
- **STT, 11 recorded clips (7 English, 4 Tamil):** with `codemix`, model names were resolved in 78 % of clips by exact alias match and 89 % with fuzzy matching (Tamil 3/3, English 5/6). With `transcribe`, 67 % (Tamil 0/3: it writes model names in Tamil script, e.g. "டியோ 125", "ஹைனஸ்"). Phone numbers were captured exactly in 2/2 clips in both modes. The only `codemix` miss was "CB350HINCE" (see Phase 2 notes in §6).
- **LLM, text-mode check, 2 runs each:** `gpt-5.4-mini` passed 10/10 both times, first-call time to first token p50 0.73–0.81 s. `gpt-4.1-mini` was faster (p50 0.44–0.49 s) but failed 3 of 20 turns: a **wrong spoken price** (Telugu "6 lakh 6 thousand" for ₹65,600), a garbled Tamil number, the `[caller_language]` tag read aloud, and Telugu script inside a Malayalam reply. **Rejected:** prices are a hard rule.

**Chosen values (Phase 1):** STT_MODE = `codemix` (`saaras:v4`, auto-detect), LLM = `gpt-5.4-mini` (`reasoning_effort=none`, Azure South India, async content filter), measured p50/p90 TTFA = **1.95 / 2.69 s** in console run 2. This meets the revised 2.0 / 3.0 s target (§1).

### 4.1 Language handling

Six languages: `hi-IN` (Hinglish), `en-IN`, `ta-IN`, `te-IN`, `ml-IN`, `kn-IN` (`SUPPORTED_LANGUAGES`). **Code decides the reply language; the LLM never guesses it** (same principle as rule 12). Measured 2026-10-15: when the prompt only said "reply in the caller's language", Tamil, Telugu and Malayalam callers got Hinglish replies. With the language passed explicitly, the replies matched (Telugu came back in Roman script once; see risks).

- **Detection:** STT runs with `language="unknown"`. Each final transcript carries a detected language (probability 1.0 on every test clip).
- **Session language (`domain/language.py`, pure, unit-tested):** starts as `GREETING_LANGUAGE` (`ta-IN`). It switches to the detected language only when a final transcript has ≥ 3 words **and** the detection probability is ≥ 0.6 (or not reported), so one-word answers like "ok", "haan" or "yes" don't flip it. (Changed in step 5 from "words **or** probability ≥ 0.9": Sarvam reported probability 1.0 on every clip, including one-word ones, so probability alone can't protect short answers.) An explicit request ("English mein bolo", "Tamil la pesunga", "తెలుగులో"), or a bare language name such as an answer to the greeting's question, always switches. If more than one language is named, nothing changes. A script check (`script_mismatch`) logs replies in the wrong script, e.g. Telugu in Roman letters, as guardrail violations.
- **LLM:** each turn's instructions include `[caller_language] <name>` plus the script rule: Hindi = Roman-script Hinglish; English = English; Tamil, Telugu, Malayalam, Kannada = native script, with brand/model names and words like *test ride*, *ex-showroom* kept in English.
- **TTS:** `language_code` = session language, updated before each reply.
- **Tools:** `spoken_price` and spoken slot times are rendered in the session language by `domain/` formatters (§9, §10); the LLM repeats them verbatim.
- **Greeting language:** Tanglish (§8), which also asks which language to continue in. The agent switches as soon as the caller speaks.
- **Storage:** the final session language is saved on each booking and lead (`language` column) so the showroom calls back in the same language.

Illustrative worker skeleton. **Verify every API name against current LiveKit docs before use.**

```python
# src/honda_voice/agent/worker.py (illustrative)
from livekit import agents
from livekit.agents import AgentSession, RoomInputOptions
from livekit.plugins import sarvam, silero

from honda_voice.agent.sales_agent import SalesAgent
from honda_voice.config import settings


async def entrypoint(ctx: agents.JobContext):
    await ctx.connect()
    session = AgentSession(
        vad=silero.VAD.load(),
        stt=sarvam.STT(model=settings.stt_model, mode=settings.stt_mode, language="unknown"),
        llm=settings.build_llm(),
        tts=sarvam.TTS(
            model=settings.tts_model,
            speaker=settings.tts_speaker,
            target_language_code=settings.greeting_language,
        ),  # updated per turn (§4.1)
        # turn_detection=MultilingualModel(),
    )
    await session.start(room=ctx.room, agent=SalesAgent(), room_input_options=RoomInputOptions())
    await session.generate_reply(instructions="Greet the caller per GREETING in prompts.py.")


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint, agent_name="honda-sales"))
```

## 5. Data model

Migrations in `data/migrations/`. Every table has RLS enabled with **no anon policies**. The backend connects as the `postgres` role through the pooler (which bypasses RLS); the browser never queries the DB directly.

### `001_init.sql` (applied to the Supabase project on 2026-10-15)

The file `data/migrations/001_init.sql` is the source of truth; this is a summary. Apply with `uv run python scripts/apply_migrations.py` (asyncpg, no psql needed); applied files are recorded in `public.schema_migrations`, and a changed file is refused.

| Table | Purpose and notable columns |
|---|---|
| `showrooms` | Open/close times, `closed_isodow`, `slot_minutes`, `tz` (feeds `check_showroom_open`) |
| `bikes` | `segment` ∈ the catalog's **17 segment values** (replaces the 5-value `category`), `division`, `fuel`, `status`; `engine_cc` **optional** (required for petrol), `range_km` for EVs; `mileage_kmpl` + `mileage_basis`; `specs` jsonb, `features`, `colours`, `unverified_fields`; ex-showroom `price_min_inr`/`price_max_inr` with `price_available`, city, date, precision, source |
| `bike_variants` | `ex_showroom_inr` nullable, `inferred`; no stored spoken price (§9) |
| `bike_on_road_prices` | On-road prices **as published**, per the source's own variant labels: `on_road_inr`, breakdown `ex_showroom_basis_inr` + `rto_inr` + `insurance_inr` (all or none) + `other_charges` jsonb (TCS 1 %, PM E-Drive subsidy…), `city`, `source`, `as_of`, `needs_review` |
| `bike_aliases` | Any-script aliases, trigram index |
| `slots` | Capacity/booked with `booked ≤ capacity` |
| `bookings` | Phone check, `status`, `language`, calendar sync columns; one active booking per phone per slot; **no consent column** |
| `leads` | Optional name/phone, `reason`, `language` |

Every table has RLS enabled with no anon policies. `tests/integration/test_schema.py` checks the tables, RLS, the `anon` role, and each constraint (each test rolls back).

### `002_booking_functions.sql`

```sql
create or replace function public.reserve_slot(
  p_slot_id bigint, p_name text, p_phone text, p_reference text,
  p_room text, p_language text
) returns bigint
language plpgsql as $$
declare v_id bigint;
begin
  update public.slots
     set booked = booked + 1
   where id = p_slot_id and booked < capacity and starts_at > now();
  if not found then
    raise exception 'SLOT_UNAVAILABLE' using errcode = 'P0001';
  end if;

  insert into public.bookings (reference, slot_id, customer_name, customer_phone, room_name, language)
  values (p_reference, p_slot_id, p_name, p_phone, p_room, p_language)
  returning id into v_id;
  return v_id;
end $$;

create or replace function public.cancel_booking(p_reference text, p_phone text)
returns boolean
language plpgsql as $$
declare v_slot bigint;
begin
  update public.bookings set status = 'CANCELLED'
   where reference = p_reference and customer_phone = p_phone and status = 'CONFIRMED'
   returning slot_id into v_slot;
  if v_slot is null then return false; end if;
  update public.slots set booked = booked - 1 where id = v_slot;
  return true;
end $$;
```

Both functions run in a single transaction, so the capacity check and the insert are atomic under concurrent callers.

## 6. Catalog (hand-curated snapshot, not scraped)

`data/catalog/<model-id>.json` (one file per model, 33 models, snapshot 2026-10-14) plus `data/catalog/_index.json` is the source. `scripts/build_catalog.py` regenerates the files; `scripts/seed_catalog.py` validates them with pydantic and upserts them. Spoken prices are not stored; tools render them per call in the session language (§9). There is no scraper.

**Coverage:** every model on honda2wheelersindia.com as of the snapshot, including EVs (Activa e:, QC1, QC3), pre-booking models (ADV160, XR300L, XR300 Rally) and the upcoming Rebel 500. Special editions (Anniversary, Limited, X-Edition, Special Edition) are variants of their base model.

**File shape (per model):** `id, name, segment, division, fuel, status (on_sale | pre_booking | upcoming), official_url, price{city, min, max, as_of, precision, source, notes}, variants[{name, ex_showroom_inr, inferred?}], specs{...}, features[], colours[], aliases[], pitch, pitch_status, notes[], sources[], unverified_fields[], snapshot_date`.

**Data-quality rules the seed script enforces:**
- Prices are ex-showroom **Delhi** from listing sites (mostly Autocar India, Oct 2026) and are rounded (`precision: "approx"`). Treat them as demo data; replace with the showroom's own price list before any real use.
- A variant with `ex_showroom_inr: null` is loaded without a price. When a caller asks for it, `find_bike` returns the model's price range instead, and the agent says the exact variant price will come from the showroom (offer a callback).
- Models with `price.min: null` (currently Unicorn, SP160, NX200) are loaded with `price_available = false`; the agent gives specs and offers a callback for price.
- `inferred: true` means the variant price was mapped from the min/max range (lowest → base variant). Verify before relying on it.
- Fields listed in `unverified_fields` come from widely published manufacturer figures that were not re-confirmed from a fetched source. `find_bike` omits them from tool output until verified (the agent must never speak an unverified spec).
- `status: "upcoming"` models can't be booked for test rides; the agent captures a lead instead.
- `pitch` is a draft (`pitch_status`); review it before the demo. It contains no competitor claims.

Schema change: `bike_variants.ex_showroom_inr` becomes nullable (there is no `spoken_price` column), and `bikes` gains `status`, `price_available boolean`, `price_min_inr`, `price_max_inr`.

**Corrections (2026-10-15, product owner):** Activa e: Standard is ₹1,18,000 ex-showroom (was ₹1,17,000 launch price), changed in `scripts/build_catalog.py` and regenerated.

**Seeding (step 9):** `uv run python scripts/seed_catalog.py` (`--dry-run` validates and reports only) runs `honda_voice.db.catalog_seed`: pydantic validation of every model, then one transaction that upserts `bikes` and replaces their variants, aliases and on-road rows, so re-running converges on the catalog exactly (an integration test checks a data fingerprint is unchanged by a re-seed). It reads three auxiliary files next to the model files:
- `_price_overrides.json`: product-owner price decisions applied on top of the generated catalog (`scripts/build_catalog.py` is kept read-only). Currently Unicorn, SP160, NX200.
- `_stt_aliases.json`: spellings STT actually produced (e.g. `hiness`, `hince`, `cb350hince`, `anicon`); extend from new recordings.
- `_on_road_delhi.json`: below.
The seed fails if two models claim the same alias, and prints a data-quality report (inferred variant prices, unverified fields, non-on-sale models, overrides, on-road rows needing review). Seeded on 2026-10-15: 1 showroom, 33 bikes, 57 variants, 173 aliases, 54 on-road prices.

**On-road prices (Delhi), `data/catalog/_on_road_delhi.json`:** 54 entries for 32 models, collected on 2026-10-15 from BikeWale's Delhi price pages: only figures shown on the page, no derived values. BikeWale gives the full breakdown (its ex-showroom figure + RTO + insurance + other charges such as TCS 1 % or the PM E-Drive subsidy) for the base variant, and only the total for other variants; every breakdown adds up to its total. Notes:
- BikeWale's ex-showroom figures differ slightly from the catalog's (e.g. Shine 100 ₹65,642 vs ₹65,640; Activa e: ₹1,20,133 vs ₹1,18,000), so they are stored as the *basis of the on-road figure*, not as catalog prices.
- BikeWale merges the CB350 H'ness into its CB350 listing; the H'ness row reuses it and carries `needs_review`.
- QC3 lists a "Delhi State Subsidy" of +₹30,000 *added* to the total; flagged `needs_review`.
- Unicorn, SP160 and NX200 had no catalog ex-showroom price; BikeWale's (₹1,15,832 / ₹1,18,060 Single Disc / ₹1,61,296) were adopted by the product owner on 2026-10-15.
- Rebel 500 (upcoming) has no on-road price.

**Phase 2 alias and matching notes (from step 7):** add the misspellings STT actually produced: `hiness`, `hince`, `cb350hince`, `cb350 hiness`, `highness` (and keep collecting them from logs). Matching must use **longest match wins**: "Activa 125" also hits the bare alias `activa` (Activa 110) and "Shine 100" hits Shine 125's `shine`, so a shorter alias contained in a longer matched alias is dropped. Keep the fuzzy/trigram threshold tight: at 0.8 similarity, "CB350 Hiness" also matched CB350C, CB500 and CB750; ambiguous fuzzy results go back to the caller as "which one?".

**Step 10 (2026-10-15): the agent reads the database.** The worker loads the whole catalog (~0.4 s) into a `Catalog` snapshot in the background while the greeting plays, so alias matching, pre-fetching, `list_bikes` and `compare_bikes` cost no database round trip mid-turn; only the trigram fallback queries Postgres (~85 ms from India). Catalog changes take effect on the next call. Results on the step 7 recordings with the live lookup (`codemix`): every expected model found in 9/9 clips (100 %); exactly the expected set in 8/9 (89 %), the difference being a clip where the caller also said "CB350" as the family name. Text-mode check: 15/15, including on-road, list, compare, a Tamil on-road breakdown and a bare "activa" (agent asks which Activa).

**Aliases in every script:** each model gets aliases in Latin script plus Devanagari, Tamil, Telugu, Malayalam and Kannada spellings (e.g. `ஆக்டிவா`, `యాక్టివా`, `ആക്ടിവ`, `ಆಕ್ಟಿವಾ`), because `codemix` usually but not always keeps model names in Latin script. Phase 1 recordings add the misspellings STT actually produces.

## 7. Tools

All tools are `@function_tool` methods with pydantic-typed arguments. Each returns a small dict meant for the LLM to speak from, never raw rows. Spoken strings (`spoken_price`, slot times) are rendered in the **session language** (§4.1).

### SalesAgent

| Tool | Args | Behavior |
|---|---|---|
| `find_bike` | `query: str` | `db/catalog.py` (step 10). **Alias match, longest wins** (`domain/alias_match.py`: "Activa 125" doesn't also hit Activa 110's bare "activa"); if an alias covers only part of the query, a clearly better **trigram** match wins ("cb350 hinees" → H'ness); otherwise trigram fallback (similarity ≥ 0.4, top must beat the runner-up by 0.1, else up to 3 candidates to ask "which one?"). A bare family name ("activa", "hornet") also returns `similar_models` so the agent asks which one. Returns `domain/catalog_view.bike_summary()`: `{found, bike_id, name, segment, status, engine_cc, mileage_kmpl, specs{every verified spec}, features?, colours?, pitch, price_available, price_min_spoken, price_max_spoken?, price_note, variants:[{variant_name, spoken_price\|null}], on_road{available, city, from_spoken, cheapest_variant, variants[], breakdown{ex_showroom, rto, insurance, other[] (deductions marked)}, note}, similar_models?, booking_note?}`. Rows flagged `needs_review` never appear. When nothing matches, the note tells the agent to pivot to the closest Honda model if the caller named a competitor. |
| `check_showroom_open` | `day_ref, time_24h?` | `day_ref` ∈ `today, tomorrow, day_after, mon…sun, YYYY-MM-DD`. Resolved in code (`datetime_resolve.showroom_status`, rule 5) against the showroom's open/close times and closed weekdays. Returns `{date, weekday, is_today, open_that_day, opens, closes, open_now?, time_asked?, open_at_time_asked?, note?}`; the LLM says the times naturally in the session language. |
| `list_bikes` | `segment?: str, max_price_inr?: int` | Up to 4 models, cheapest first (one of the 17 segments; budget as a plain rupee number), each with `ex_showroom_from_spoken` and `on_road_from_spoken`. |
| `compare_bikes` | `a: str, b: str` | Both resolved like `find_bike` (Honda only; unresolved → ask which). Returns key fields side by side: segment, engine, mileage, ex-showroom range, on-road from, and verified key specs. |
| `start_booking` | `bike_id?: str` | Hands off to `BookingAgent` with chat context. |
| `capture_lead` | `name?, phone?, interest?, reason` | Fallback path; writes to `leads`. |

### BookingAgent

| Tool | Args | Behavior |
|---|---|---|
| `check_slots` | `bike_id, day_ref, time_24h?` | `day_ref` ∈ `today, tomorrow, day_after, mon…sun, YYYY-MM-DD`. Resolves via `datetime_resolve` (§10). Returns up to 3 nearest open slots as spoken strings in the session language ("kal shaam 5 baje", "நாளை மாலை 5 மணி"). |
| `validate_phone` | `digits: str` | Normalizes (strips +91/0/spaces), validates `^[6-9]\d{9}$`, returns `readback` grouped 5+5. |
| `book_test_ride` | `slot_id, name, phone, caller_confirmed: bool` | Rejects if `caller_confirmed` is false. When `DECISION_MODE=enforce`, also requires the decision-model confirmation gate to pass (§20.3). Calls `reserve_slot` (with the session language). On success, schedules the calendar mirror task and returns `{reference_spoken}` (spelled with gaps: "H N D, 7 K 3 Q 9"). On `SLOT_UNAVAILABLE`, returns the next 2 open slots. |
| `cancel_test_ride` | `reference, phone` | Calls `cancel_booking`. |
| `back_to_sales` | — | Hands back to `SalesAgent`. |

## 8. Conversation design (`agent/prompts.py`)

**Persona:** a friendly, concise showroom executive. The voice is female (`aarti_hi_customer`), so first-person Hindi verbs use feminine forms ("kar sakti hoon", "confirm kar rahi hoon"); if `TTS_SPEAKER` changes to a male voice, switch the forms in `prompts.py` too. Reply in the session language given each turn (§4.1), never by guessing: Hindi as Roman-script Hinglish; Tamil, Telugu, Malayalam and Kannada in native script; brand/model names and words like *test ride* and *ex-showroom* in English. Feminine first-person forms apply in every language that marks gender on verbs. `prompts.py` holds the per-language fixed lines (greeting, intro, re-confirm, competitor pivot, out-of-scope deflection).

**Opening (changed 2026-10-15 after the first console test, at the product owner's request: no recording-consent question anywhere):**
1. **Greeting (Tanglish, fixed text, no LLM wait):** "வணக்கம்! Honda showroom-க்கு welcome. Tamil, English, Hindi, Telugu, Kannada, Malayalam, எந்த language-ல பேசலாம்?" It is stored in Tamil script with English words, because TTS reads that natively.
2. **The caller's first reply sets the session language** (§4.1).
3. **Intro, in that language, on the first caller turn only:** what the agent can do (answer questions about Honda bikes and scooters, book a test ride, check whether the showroom is open at a given time), then an open question such as "What would you like to know?". If the caller's first reply already contains a question, the agent answers it instead of the intro. The code decides when the intro line is offered, not the LLM.

**Answer only what was asked:** one or two facts per turn. The agent doesn't recite specs, features or the pitch unprompted; for a general "tell me about X" it gives one or two highlights and asks what the caller wants to know. `find_bike` returns every verified spec, plus features and colours, so specific questions (ground clearance, kerb weight, brakes, tyres…) can be answered.

**Brevity:** at most 2 short sentences per turn, and end with a question that moves the call forward. This is enforced through the prompt and `max_tokens`. Over-length turns are **logged, not truncated**.

**Prices:** speak only spoken prices from tool output, always saying which kind: "ex-showroom" or "on-road (Delhi)". On-road prices and their breakdown (RTO, insurance, other charges) come from `bike_on_road_prices` and are worded by `format_inr_spoken` like any other price; say they are for Delhi and vary by city, and offer the showroom's exact quote. Rows with `needs_review` are never spoken.

**Unknown facts:** if a tool doesn't return the answer, say so and offer a callback. Never guess specs.

**Competitor policy:** if a caller mentions another brand, acknowledge it neutrally, make **no claims** about the other product, and pivot to the closest Honda model with facts from `find_bike`. Example (Hinglish; `prompts.py` has one per language): "Achha, aap scooter dekh rahe hain — Honda mein Activa 125 is segment mein hai, uska mileage aur price bataun?" Do not refuse or lecture.

**Out of scope** (service, insurance, unrelated topics): one-line deflection plus offer a callback lead.

**Booking script** (BookingAgent), one field at a time:
1. Model (pre-filled if known), then day and time, then offer up to 3 slots.
2. Name, then read it back.
3. Phone, then `validate_phone`, then read back grouped 5+5. Re-ask on failure; after 2 failed tries, offer `capture_lead`.
4. A single confirmation summary in the session language ("Activa 125, kal shaam 5 baje, naam Ravi, number 98765 43210 — confirm karun?"). Call `book_test_ride(caller_confirmed=True)` only after an explicit yes.
5. Speak the reference code slowly and say an SMS/WhatsApp would follow in production (not built in the POC).

## 9. Speech text transforms (`agent/text_transforms.py`)

Applied in an overridden `tts_node` as **streaming** transforms (chunk-in, chunk-out; carry a small buffer only across token boundaries).

1. Strip markdown (`*`, `#`, bullets), emojis, and URLs.
2. Backstop for currency: if a raw `₹` amount slips through, rewrite it with `format_inr_spoken(amount, session_language)`. Count it as a guardrail violation in metrics, because the prompt should have prevented it.
3. Expand "km/l" → "kilometre per litre", "cc" → "C C".
4. No truncation; over-length turns are only logged.

**`domain/pricing.format_inr_spoken(amount: int, lang: str) -> str`** is the single canonical formatter for every language:
- Rounding (all languages): `< 1,00,000` → nearest 100; `≥ 1,00,000` → nearest 1,000. When rounding changed the value, prefix the language's "approximately" word: lagbhag / about / சுமார் / సుమారు / ഏകദേശം / ಸುಮಾರು.
- `hi-IN`: "74 hazaar 6 sau rupaye", "1 lakh 45 hazaar rupaye", "2 lakh rupaye" (exact thousands/lakhs drop the smaller units).
- `en-IN`: "74,600 rupees", "1 lakh 45 thousand rupees".
- `ta-IN`, `te-IN`, `ml-IN`, `kn-IN`: Indian-grouped numerals plus the native rupee word: "80,600 ரூபாய்" / "80,600 రూపాయలు" / "80,600 രൂപ" / "80,600 ರೂಪಾಯಿ". Bulbul read these numerals as correct native number words in the 2026-10-15 test.
- Digits stay as numerals in Hindi too. Any pronunciation fix goes inside this function only.
- **Verified 2026-10-15 (step 3):** ₹65,640, ₹1,51,600 and ₹42,82,000 were formatted and spoken by `aarti_hi_customer` (`bulbul:v4-flash`), then transcribed back with `saaras:v4`, in all six languages. All 18 came back as the right amount in native number words, with the "approximately" word intact. Indian digit grouping (`1,52,000`) is what makes TTS say "lakh" in each language. A human listening check is still part of Phase 1.

## 10. Date, time, and phone handling (`domain/`)

`datetime_resolve.resolve(day_ref, time_24h, now_ist, showroom) -> list[datetime]`:
- The LLM converts phrases like "shaam 5 baje" to `time_24h="17:00"`. The prompt includes the time-of-day mapping in every language: morning 09–12 (subah / kaalai / poddhuna / raavile / beLigge), afternoon 12–16 (dopahar / madhiyam / madhyaahnam / uchakku / madhyaahna), evening 16–19 (shaam / saayangaalam / saayantram / vaikunneram / saanje).
- The code resolves the day relative to `now_ist`, rejects past or closed days, and snaps to the slot grid (`slot_minutes`) within open hours.
- If no time is given, it returns the next 3 open slots on that day.
- The current IST date, weekday, and time are injected into the BookingAgent instructions each turn.

`datetime_resolve.speak_slot(dt, now_ist, lang) -> str` renders a slot in the session language ("kal shaam 5 baje", "நாளை மாலை 5 மணி", "tomorrow 5 pm"). It is pure and unit-tested per language.

`phone.normalize(raw) -> str | None` and `phone.readback(p) -> "98765 43210"`. Digits are language-neutral; TTS reads them in the session language. `normalize` also accepts native-script digits (e.g. Devanagari `९८७६५…`).

## 11. Google Calendar mirror

- Create a dedicated calendar ("Honda Demo – Test Rides") in a normal Google account, and share it with the service account email with "Make changes to events". Put its ID in `GOOGLE_CALENDAR_ID`. **Do not use `primary`**; that is the service account's own invisible calendar.
- Do not add attendees (that requires domain-wide delegation). Put customer details in the event description.
- After `reserve_slot` commits, a background task creates the event (3 retries with backoff) and updates `calendar_event_id` / `calendar_sync_status`. A failure sets `FAILED` and is never spoken to the caller. `scripts/retry_calendar_sync.py` re-pushes `PENDING`/`FAILED` rows. Cancellation deletes the event the same way.
- No free/busy check. Slot availability comes only from the `slots` table.

## 12. Frontend (`frontend/`)

- Start from LiveKit's **agent-starter-react** template. It provides the visualizer, control bar, transcription, and connection handling. Customize the branding and add a panel.
- `app/api/connection-details/route.ts` (or the template's equivalent): issues a token with a room configuration that **explicitly dispatches** `agent_name="honda-sales"`. Protect it with a demo passcode (`DEMO_PASSCODE`) and a short token TTL.
- `app/api/bookings/route.ts`: server-side query (server-only env vars) returning today's and upcoming bookings with names masked ("Ravi K.", "98xxx xx210") and the booking language. The panel polls every 5 s.
- `app/api/catalog/route.ts`: models and base ex-showroom prices (₹ numerals) for a side panel.
- Transcripts come from the agent session's built-in transcription; no custom data channel is needed.

## 13. Configuration

### `.env.example` (backend)

```bash
# LiveKit Cloud (cloud.livekit.io → project → Settings → Keys). Phase 10 switches to local (§24).
LIVEKIT_URL=wss://<project>.livekit.cloud
LIVEKIT_API_KEY=<key>
LIVEKIT_API_SECRET=<secret>

SARVAM_API_KEY=<key>
STT_MODEL=saaras:v4              # saaras:v3 | saaras:v4
STT_MODE=codemix                 # decided in Phase 1 (preliminary winner: codemix)
STT_LANGUAGE=unknown             # auto-detect; per-turn language feeds §4.1
TTS_MODEL=bulbul:v4-flash        # bulbul:v3 | bulbul:v4-flash
TTS_SPEAKER=aarti_hi_customer    # female voice for all six languages; v4-flash names differ from v3 ('shubh')
SUPPORTED_LANGUAGES=hi-IN,en-IN,ta-IN,te-IN,ml-IN,kn-IN
GREETING_LANGUAGE=ta-IN          # greeting is Tanglish (§8); switches when the caller speaks
SARVAM_TTS_DICT_ID=              # optional pronunciation dictionary

LLM_PROVIDER=azure               # azure | openai | groq | sarvam
LLM_MODEL=gpt-5.4-mini           # underlying model name
LLM_REASONING_EFFORT=none        # none | minimal (gpt-5 / gpt-5-mini only)
LLM_MAX_TOKENS=150               # max_completion_tokens per reply
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com
AZURE_OPENAI_API_KEY=<key>
AZURE_OPENAI_DEPLOYMENT=<deployment-name, e.g. voice-gpt-5.4-mini>
AZURE_OPENAI_DEPLOYMENT_ALT=<Phase 1 comparison, e.g. voice-gpt-4.1-mini>
AZURE_OPENAI_API_VERSION=2024-12-01-preview   # verified 2026-10-15; async filter needs ≥ 2024-02-01
OPENAI_API_KEY=
GROQ_API_KEY=

# Decision layer (Phase 7) — Laya, self-hosted (services/laya/run.sh, §23); fallback: LLM structured output
DECISION_PROVIDER=laya           # laya | llm_fallback
DECISION_MODE=off                # off | shadow | enforce
DECISION_TIMEOUT_MS=600
LAYA_BASE_URL=http://127.0.0.1:8100   # local laya-serve, Jev-compatible POST /v1/systemone
LAYA_MODEL=multilingual          # Laya checkpoint for Indic/code-mixed turns: multilingual | english
LAYA_API_KEY=                    # only if laya-serve is started with LAYA_API_KEY

# Optional API service (§22) — when enabled, the frontend calls it instead of the DB
API_BASE_URL=                    # e.g. http://localhost:8000; empty = Next.js routes read the DB
API_SERVICE_TOKEN=<shared secret between Next.js server and FastAPI>

# LangGraph (Phase 8) — native | langgraph
ORCHESTRATOR=native
LG_ROUTER=decision               # decision | rules | llm

# Supabase session pooler (IPv4): Project Settings → Database → Connection string → Session pooler.
# Phase 11 switches to local Postgres (§24).
DATABASE_URL=postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres

SHOWROOM_ID=demo-showroom
BUSINESS_TZ=Asia/Kolkata

CALENDAR_SYNC=on                 # on | off (§24.1)
GOOGLE_CALENDAR_ID=<calendar-id>@group.calendar.google.com
GOOGLE_SERVICE_ACCOUNT_JSON=secrets/google_service_account.json
```

### `frontend/.env.local.example`

```bash
LIVEKIT_URL=wss://<project>.livekit.cloud
NEXT_PUBLIC_LIVEKIT_URL=wss://<project>.livekit.cloud
LIVEKIT_API_KEY=<key>
LIVEKIT_API_SECRET=<secret>
DATABASE_URL=<same pooler URL, server-side only>
DEMO_PASSCODE=<choose one>
```

`.gitignore` must include `.env`, `frontend/.env.local`, `secrets/`, and `evals/recordings/`.

## 14. Build phases (each with exit criteria)

Ordered to remove the biggest risk (voice quality and latency) first.

**Phase 0 — Setup.** Rotate any credential that appeared in earlier drafts. Create a LiveKit Cloud project and use the existing Supabase project (reset its DB password; check its region is near India; use the session pooler). Create the repo, `.gitignore`, `.env.example`, and a uv project with the `src/` layout, ruff, and pytest.
*Exit:* `uv run pytest` passes on an empty suite, and no secrets are in git history.

**Phase 1 — Voice spike (console mode).** Run the worker with an in-memory `find_bike` over 3 hardcoded models, with language detection and per-turn reply language (§4.1). Record 30 utterances mentioning model names (10 Hindi/English, 5 each Tamil, Telugu, Malayalam, Kannada) and compare STT modes. Benchmark LLM candidates. Check how Bulbul pronounces prices, model names, and phone numbers in each language.
**Interim (2026-10-15):** only English and Tamil speakers are available, so the first recording set is 10 clips (5 English, 5 Tamil); Hindi, Telugu, Malayalam and Kannada clips are added when speakers are available, and the per-language ≥ 80 % target applies only to recorded languages until then.
*Exit:* STT mode, LLM, and the price-speech format are recorded in §4/§9; p50 / p90 TTFA ≤ 2.0 / 3.0 s in console (revised, §1); per-turn metrics are logged; a console call that switches language mid-call gets replies in the new language.

**Phase 2 — Catalog in Supabase.** Migrations 001, load `data/catalog/*.json` (33 models), the seed script, aliases fed by the Phase 1 recordings, `find_bike`/`list_bikes`/`compare_bikes` against the DB.
*Exit:* model-name resolution ≥ 90 % on the recorded set; catalog unit and integration tests pass.

**Phase 3 — Booking.** Migration 002, slot generation, BookingAgent with handoff, date/phone domain modules, read-back flow, lead fallback, cancellation.
*Exit:* concurrency test (20 parallel `reserve_slot` calls on a capacity-1 slot leads to exactly 1 success); booking evals pass.

**Phase 4 — Calendar mirror.** Shared calendar, background sync, retry script.
*Exit:* a booking appears in the shared calendar within 10 s, and a simulated calendar outage leaves the booking confirmed with status `FAILED` until the retry script fixes it.

**Phase 5 — Frontend.** Starter template, token dispatch with passcode, bookings and catalog panels.
*Exit:* a full call from the browser books a ride that appears in the panel within 5 s.

**Phase 6 — Evals and hardening.** The full eval suite, a latency report, and a demo script.
*Exit:* all §1 targets met; README with setup steps.

**Phase 7 — Decision layer, self-hosted Laya (§20, §23).** `DecisionModel` interface + Laya client (local `laya-serve`) + LLM fallback; migration 003; parallel per-turn signals; confirmation gate. Run in `shadow` mode across the full eval suite and demo calls, then switch to `enforce` only for gates that clear their targets.
*Exit:* §20.5 targets met on the labelled set; p50 TTFA unchanged (±50 ms) vs Phase 6 on non-confirmation turns.

**Phase 8 — LangGraph orchestration, A/B (§21).** Build the graph behind `ORCHESTRATOR=langgraph` without removing the native path. Run the latency A/B across three router configs.
*Exit:* latency report comparing configs in §21.3; decision recorded here (adopt / adopt with decision-model router / stay native) with reasons.

**Phase 9 — Optional FastAPI service (§22).** Only if a trigger in §22.1 applies.
*Exit:* frontend works with `API_BASE_URL` set and with it empty; Next.js no longer holds `DATABASE_URL` when the API is enabled; API tests pass.

**Phase 10 — Self-hosted LiveKit (§24).** Replace LiveKit Cloud with the open-source server in Docker. Only `LIVEKIT_URL` / keys change; no code changes.
*Exit:* full browser call and booking work against `ws://localhost:7880`; latency report re-run and compared with Cloud.

**Phase 11 — Local Postgres (§24).** Replace hosted Supabase with `postgres:16` in Docker, migrations applied on first start, catalog re-seeded. Only `DATABASE_URL` changes.
*Exit:* all integration tests and evals pass against local Postgres; data export/import path from Supabase documented.

Jev (TypeSafe AI, paid) was dropped on 2026-10-15 in favour of Laya, which is open source and runs on the local GPU (§23). Laya is part of Phase 7, not a separate phase.

## 15. Testing and evals

**Unit tests (`tests/`, no network):**
- `test_pricing.py`: rounding, lakh/hazaar boundaries, the "approximately" prefix, each of the six languages.
- `test_language.py`: session-language rule (short answers don't switch, ≥ 3-word or high-probability turns do, explicit requests do).
- `test_datetime_resolve.py`: today/tomorrow/weekday across week boundaries, past dates, closed days, opening-hour boundaries, IST vs UTC, `showroom_status`; later snapping and `speak_slot` in each language.
- `test_phone.py`: +91, leading 0, spaces, native-script digits, invalid first digit, wrong length.
- `test_text_transforms.py`: markdown stripping, ₹ backstop, chunk boundaries in streaming.
- `test_catalog_lookup.py`: alias exact match, trigram fallback, ambiguous results.

**Integration tests (`-m integration`, Supabase test project):**
- `test_reserve_slot.py`: capacity, the concurrency race, past slots, double-booking by the same phone, cancellation restoring capacity.
- `test_calendar_sync.py`: mocked Google API, retry and failure paths.

**Conversation evals (`evals/`):**
- `scenarios/*.yaml`: 35 scripted text conversations: the original 25 (price inquiry ×5, comparison ×3, competitor mention ×3, booking happy path ×5, booking with a misheard phone ×3, slot taken ×2, cancellation ×2, out of scope ×2) in Hinglish/English, plus a price inquiry and a booking happy path in each of Tamil, Telugu, Malayalam and Kannada (×8), plus 2 mid-call language switches. Each lists expected tool calls and assertions: the spoken price equals the catalog value in that language, no booking without confirmation, turn length, and reply language/script matches the session language.
- `run_text_evals.py` drives the agents through LiveKit's text/testing interface, with no audio.
- `evals/recordings/`: 30 audio clips across the six languages for the STT and model-name check (git-ignored; share them separately).

## 16. Observability

- Per turn, log: room, agent, detected language + probability, session language (and switches), STT final latency, LLM TTFT, TTS TTFB, end-to-end TTFA, tool calls with duration, word count, and guardrail violations (including reply-script mismatches).
- `scripts/latency_report.py` aggregates p50/p90 from the logs for the Phase 6 report.
- Mask phone numbers in logs (last 3 digits only).

## 17. Privacy

- There is no spoken recording-consent step (removed 2026-10-15 at the product owner's request), and agent sessions are not recorded (`record=False`). **Open item:** name and phone are still personal data under India's DPDP Act, so show a short privacy notice in the frontend before a call starts, and in the README.
- Collect only name, phone, and preferred slot. Mask phone numbers in logs and the UI.
- If recordings are enabled, store them for at most 30 days in the POC and document this in the README. (India's DPDP Act applies to the personal data collected.)

## 18. Project structure

```text
honda-voice-poc/
├── CLAUDE.md
├── docs/SPEC.md
├── README.md
├── pyproject.toml / uv.lock
├── .env.example
├── docker-compose.yml          # Phase 10/11 only: local LiveKit + Postgres (§24)
├── .gitignore
├── services/laya/run.sh        # starts the local Laya decision server (§23)
├── data/
│   ├── catalog/                # one JSON per model + _index.json
│   └── migrations/
│       ├── 001_init.sql
│       └── 002_booking_functions.sql
├── scripts/
│   ├── build_catalog.py        # regenerates data/catalog/
│   ├── apply_migrations.py     # applies data/migrations in order, records them
│   ├── seed_catalog.py         # validate + upsert the catalog (wraps db/catalog_seed.py)
│   ├── generate_slots.py
│   ├── retry_calendar_sync.py
│   └── latency_report.py
├── src/honda_voice/
│   ├── __init__.py
│   ├── config.py                 # pydantic-settings; builds LLM from env
│   ├── db/
│   │   ├── pool.py               # asyncpg pool (session pooler)
│   │   ├── catalog.py            # find/list/compare queries
│   │   ├── catalog_seed.py       # catalog JSON → validated rows → transactional upsert
│   │   └── bookings.py           # reserve/cancel/lead/slot queries
│   ├── domain/
│   │   ├── pricing.py            # format_inr_spoken(amount, lang)
│   │   ├── language.py           # session-language rule, per-language words (§4.1)
│   │   ├── datetime_resolve.py   # resolve + speak_slot(lang)
│   │   └── phone.py
│   ├── integrations/
│   │   └── gcalendar.py          # create/delete event, retry
│   ├── decisions/                # Phase 7
│   │   ├── base.py               # DecisionModel protocol (choice / score / probability)
│   │   ├── laya_client.py        # Laya (local laya-serve) implementation, timeout (§23)
│   │   ├── llm_fallback.py       # same interface via LLM structured output (baseline)
│   │   └── questions.py          # typed question definitions (§20.2)
│   ├── graph/                    # Phase 8
│   │   ├── state.py
│   │   ├── router.py             # decision | rules | llm
│   │   └── build.py              # compiled graph + LiveKit adapter wiring
│   ├── api/                      # optional FastAPI service (§22, Phase 9)
│   │   ├── main.py
│   │   ├── routes/{token,bookings,catalog,webhooks}.py
│   │   └── auth.py               # service-token check
│   └── agent/
│       ├── prompts.py
│       ├── sales_agent.py
│       ├── booking_agent.py
│       ├── text_transforms.py    # streaming tts_node transforms
│       ├── metrics.py
│       └── worker.py             # entrypoint, AgentSession config
├── frontend/                     # from LiveKit agent-starter-react
│   └── app/api/{connection-details,bookings,catalog}/route.ts
├── evals/
│   ├── scenarios/*.yaml
│   ├── recordings/               # git-ignored
│   └── run_text_evals.py
└── tests/
```

## 19. Open questions and risks

| Item | Risk | Resolution point |
|---|---|---|
| STT mode output for brand names | Lookups miss | Phase 1 recordings + alias table in every script |
| LLM writes Telugu (or another language) in Roman script | TTS mispronounces; transcript inconsistent | Script rule in the prompt plus few-shot lines; log script mismatches (§16); eval assertion |
| Language flip-flop on short or mixed utterances | Agent switches language mid-booking | ≥ 3-word / high-probability rule in `domain/language.py`; unit tests + 2 switch scenarios |
| LiveKit Sarvam plugin can't change TTS language or expose STT language per turn | Single-language agent | Verify in step 2; fall back to a custom `tts_node`/STT wrapper calling the Sarvam SDK |
| Turn detector support for all six languages | Agent cuts callers off mid-sentence | Phase 1; fall back to STT-based turn detection |
| LLM Hinglish tool-calling reliability | Wrong args, extra turns | Phase 1 benchmark + eval suite |
| Bulbul pronunciation of numbers and codes | Unclear prices or reference codes | Phase 1 listening test; pronunciation dictionary |
| Package versions (`livekit-agents`, Sarvam plugin) | API drift from snippets | Verify against docs; pin in pyproject |
| Catalog freshness | Wrong prices in demo | `price_as_of` spoken via "ex-showroom" note; re-seed before demo |
| Laya accuracy on code-mixed Indic transcripts (Tamil, Telugu, Kannada, Malayalam aren't benchmarked in its README) | Wrong routing or gate decisions | Shadow mode + per-language labelled set before enforce (§20.5); `llm_fallback` per question |
| Laya is young (0.4.x, frequent releases) | API or behaviour changes | Pin `laya==0.4.7`; `DecisionModel` interface with LLM fallback; gate fails safe to re-confirmation |
| LangGraph adds per-turn latency | Slower agent | Phase 8 A/B; non-LLM router; no buffering nodes |
| Laya needs the local machine's GPU | Slower or unavailable gate on other machines | CPU fallback (~0.2–0.5 s per call per README); timeout + fail-safe; provider switchable to `llm_fallback` |
| A second backend service (FastAPI) | More to deploy and keep in sync | Optional; shared `db/` + `domain/` modules; only behind §22.1 triggers |

---

## 20. Decision layer (Phase 7, Laya)

Laya (Convai Innovations, Apache-2.0) is a non-generative "System One" model: given a context and typed questions, it returns a **choice** from fixed options, a **score** on a rubric, or a **probability** that a statement is true, each with a confidence value. It cannot produce free text, so it complements the LLM rather than replacing it. **Use Laya for judgments, code for rules, and the LLM for replies.**

It runs self-hosted (§23): `laya==0.4.7`, checkpoints `laya-multilingual` (mmBERT-base, 322 M params) and `laya` (ModernBERT-large, 421 M), served by `laya-serve` on the local GPU behind the Laya-compatible `POST /v1/systemone` API.

The decision layer is **provider-neutral**: `DECISION_PROVIDER` picks the implementation behind the `DecisionModel` interface (`laya` or `llm_fallback`); thresholds and gates don't change.

### 20.1 Where Laya sits

| Use | Path | Effect on latency |
|---|---|---|
| Per-turn signals (intent, competitor, out-of-scope, frustration, wants-human, purchase intent) | **Parallel**: fired when the user turn completes, alongside the LLM. Results apply from the *next* turn and to logging. | None on the current turn |
| Booking-confirmation gate | **Serial**, only on the turn where the caller answers the confirmation summary | One Laya call (~30–60 ms per question on the local GPU; measured in Phase 7) on one turn per booking |
| LangGraph router (Phase 8) | Serial, every turn when `ORCHESTRATOR=langgraph` and `LG_ROUTER=decision` | One Laya call per turn |

Laya never decides prices, dates, slot availability, or phone validity. Those stay in `domain/` and the DB.

### 20.2 Questions (`decisions/questions.py`)

Context = the last 4 turns of transcript plus the agent's current state (`awaiting_confirmation`, `active_agent`).

| Key | Primitive | Options / rubric |
|---|---|---|
| `intent` | choice | `catalog_question, price_question, compare, book, cancel, competitor_mention, out_of_scope, smalltalk, wants_human` |
| `confirms_booking` | probability | "The caller clearly agreed to proceed with the booking exactly as summarised (model, day, time, name, number)." Asked only when `awaiting_confirmation`. |
| `corrects_detail` | choice | `none, model, day_time, name, phone`. Asked only when `awaiting_confirmation`. |
| `frustration` | score 1–5 | 1 = calm … 5 = angry or about to hang up |
| `purchase_intent` | score 1–5 | 1 = just browsing … 5 = ready to buy this month |

All questions for one turn go in **one** request.

### 20.3 How signals are used (`DECISION_MODE=enforce`)

- **Confirmation gate:** `book_test_ride` proceeds only if `confirms_booking ≥ 0.85` and `corrects_detail = none`. Between 0.5 and 0.85, or on timeout or error, the agent re-confirms once more ("Bas confirm kar rahi hoon — kal shaam 5 baje, Activa 125, theek hai?"). Below 0.5, or any correction, the agent goes back to the field in question. This catches answers like "haan… nahi ruko, kal nahi" where the LLM might still set `caller_confirmed=True`.
- **Escalation:** `wants_human` (confidence ≥ 0.8), or `frustration ≥ 4` on two consecutive turns, injects a note into the next turn's instructions so the agent offers a showroom callback via `capture_lead`.
- **Lead scoring:** the maximum `purchase_intent` in the call is stored on the booking or lead and shown in the frontend panel (hot / warm / cold).
- **Competitor / out-of-scope:** logged and used for analytics only. The prompt handles the reply; Laya does not override it.

`DECISION_MODE=shadow`: run every question, log the results, and change nothing. `off`: no calls.

### 20.4 Storage — `003_call_signals.sql`

```sql
create table public.call_signals (
  id          bigserial primary key,
  room_name   text not null,
  turn_index  int  not null,
  question    text not null,
  answer      text not null,          -- option, score, or probability as text
  confidence  numeric(4,3),
  latency_ms  int,
  model       text not null,          -- Laya checkpoint + version, or 'llm-fallback'
  created_at  timestamptz not null default now()
);
alter table public.call_signals enable row level security;

alter table public.bookings add column purchase_intent smallint;
alter table public.leads    add column purchase_intent smallint;
```

### 20.5 Evaluation before `enforce`

Build a labelled set from the eval scenarios and demo transcripts:
- 60 single turns labelled for `intent`, covering all six languages (at least 6 per language), including mis-transcribed brand names.
- 40 confirmation-turn answers across the six languages, including tricky ones ("haan theek hai", "haan par number galat hai", "ek minute", "nahi kal nahi parson", "ok ok", "aamaa, aana number thappu").

Compare Laya against `llm_fallback.py` (same questions through the LLM's structured output) on accuracy, calibration, and latency.

| Gate | Target to enable |
|---|---|
| Confirmation gate | 0 false accepts on the 40 confirmation turns; ≤ 10 % unnecessary re-confirmations |
| Intent (analytics, escalation) | ≥ 90 % accuracy; p90 latency ≤ 500 ms |
| Frustration / purchase-intent scores | Agreement within ±1 of human label on ≥ 80 % |

If Laya misses a target, keep that question in `shadow` or switch it to `llm_fallback`.

---

## 21. LangGraph orchestration (Phase 8, future)

### 21.1 When LangGraph is worth adding

Native LiveKit handoffs cover the POC. LangGraph earns its cost if the product grows into: multi-step flows with branches (finance or EMI pre-qualification, exchange-vehicle valuation), human-in-the-loop interrupts (a sales manager approves a discount), durable state across calls (callbacks that resume a conversation), or parallel tool branches.

### 21.2 Design constraints

- Graph: `route → sales_node | booking_node → END`. Each node is a **streaming** LLM node with the same tools as §7.
- **No guardrail node.** Speech transforms stay in `tts_node` (§9). A node that post-processes the full reply forces buffering and erases streaming.
- Router: `LG_ROUTER=decision` (Laya `intent` question mapped to a node), `rules` (sticky state: stay in the current node unless a handoff tool fired), or `llm`. Prefer `decision` or `rules`.
- Checkpointer: in-memory per call in the hot path. Persist to Postgres only at call end (or asynchronously), never synchronously per turn.
- Wire into LiveKit through its LangChain/LangGraph adapter (verify the current API) so STT, TTS, VAD, and turn detection stay unchanged.

### 21.3 Latency A/B

These are **estimates to check, not measurements**. Baseline is the native setup from Phase 1–6 (measured p50 TTFA ≈ 1.95 s at the end of Phase 1).

| Config | Added per-turn latency (estimate) | Why |
|---|---|---|
| Native LiveKit handoffs | 0 (baseline) | Handoff costs an LLM call only on the turn that switches agent |
| LangGraph + `rules` router | ~5–50 ms | Graph execution and adapter overhead only |
| LangGraph + `decision` router | ~30–100 ms | One local Laya call before the main LLM |
| LangGraph + `llm` router | ~300–900 ms | A full non-streamed structured LLM call before the main LLM |
| Any config + buffering guardrail node | +0.5–2 s more | Waits for the whole reply before TTS starts |

Benchmark: `uv run python -m evals.latency_ab --configs native,lg_rules,lg_decision,lg_llm`. Run the 25 scenarios × 3 repetitions per config through the text harness, plus 10 live audio calls per config. Report p50/p90 TTFA, routing accuracy, and task success. Adopt LangGraph only if p50 TTFA rises by ≤ 150 ms, or if a feature from §21.1 justifies the cost.

---

## 22. Optional FastAPI service (Phase 9)

### 22.1 Why it's optional

The POC is already split: the **Python LiveKit worker** is the backend, and the **Next.js app** is the frontend. They don't call each other over HTTP; audio and transcripts travel over WebRTC through LiveKit. The browser only needs three small read endpoints (token, bookings, catalog), and Next.js server routes cover them. A FastAPI service adds a deployment without adding a feature, so it is **off by default**.

Add it when any of these become true:
- More than one client needs the backend (mobile app, dealer CRM, a web booking form).
- Telephony (SIP) or LiveKit webhooks need a Python endpoint.
- You want **no database credentials in the Next.js app at all** (cleaner security boundary).
- The team prefers to keep all server logic in Python.

### 22.2 Design

```text
Browser ──HTTPS──► Next.js server routes ──HTTPS + service token──► FastAPI (src/honda_voice/api)
                                                                       │ shares db/ + domain/
Browser ──WebRTC──► LiveKit Cloud ◄──► Python agent worker ───────────┘──► Supabase Postgres
```

- Lives in the same package (`src/honda_voice/api/`) and **reuses** `db/` and `domain/`. No duplicated queries or pricing logic.
- Run: `uv run uvicorn honda_voice.api.main:app --port 8000`. Deployed as its own process; it never sits in the voice path.
- Next.js keeps its routes, which become thin proxies when `API_BASE_URL` is set. The browser never calls FastAPI directly, so there is no CORS setup and the service token stays server-side.

| Endpoint | Purpose | Notes |
|---|---|---|
| `POST /token` | LiveKit token with agent dispatch (`agent_name="honda-sales"`) | Checks demo passcode; short TTL |
| `GET /bookings?from=` | Upcoming bookings for the panel | Names and phones masked |
| `GET /catalog` | Models, base spoken prices, status | Read-only |
| `POST /bookings/{ref}/cancel` | Staff cancel | Calls `cancel_booking()` |
| `POST /webhooks/livekit` | Room/participant events | Verify LiveKit webhook signature |
| `GET /healthz` | Liveness | |

Rules:
- Every route requires `Authorization: Bearer $API_SERVICE_TOKEN`, except `/healthz` and the signed webhook.
- Booking **creation** stays in the agent's `book_test_ride` tool. The API exposes no "create booking" endpoint in the POC, so all bookings go through the read-back and confirmation flow.
- Pydantic response models; asyncpg pool shared via FastAPI lifespan.

### 22.3 Tests

`tests/api/test_routes.py` with `httpx.AsyncClient`: auth required, masking applied, token contains the dispatch config, webhook signature rejected when invalid.

---

## 23. Laya, self-hosted (decision provider for §20)

### 23.1 What it is and why local

Laya (Convai Innovations, [github.com/NandhaKishorM/laya](https://github.com/NandhaKishorM/laya), Apache-2.0) is a non-generative decision model: given text and typed questions it returns `choice`, `score`, or `noul` (P(true)) answers with confidences, in one forward pass. It replaces Jev (TypeSafe AI), which is paid and was dropped on 2026-10-15. Running it locally removes the per-call cost, the 2026-10-31 free-tier deadline, and the network hop.

| Checkpoint | Encoder | Params | Use here |
|---|---|---|---|
| `laya-multilingual` | mmBERT-base | 322 M | Default: Hindi/Hinglish and the four South Indian languages |
| `laya` | ModernBERT-large | 421 M | English-only turns, if it measures better there |

README figures: ~33 ms per question on a T4 GPU, 192–464 ms per call on CPU. The README reports 48 of 51 benchmarked languages above 3× random, but names none of Tamil, Telugu, Malayalam, Kannada, so the labelled set (§20.5) must cover each of them.

### 23.2 Running it

- Own virtual environment in `services/laya/.venv` (git-ignored) with `laya[serve]==0.4.7` and PyTorch (CUDA), so PyTorch stays out of the agent's environment.
- `services/laya/run.sh` starts `laya-serve` on **127.0.0.1:8100** (8000 is reserved for §22), device `cuda`, preloading `english,multilingual`; weights (~2.3 GB) cache in `services/laya/models/` (git-ignored).
- Development machine (2026-10-15): NVIDIA RTX A3000 Laptop GPU (6 GB), 31 GB RAM; CUDA detected by PyTorch 2.14.
- Phases 10–11 (§24): add it to `docker-compose.yml` as a GPU service, or run it on CPU.

### 23.2a First smoke test (2026-10-15, `laya-multilingual`, local GPU)

Two questions per call (`confirms_booking` noul + `corrects_detail` choice) on 11 confirmation answers in all six languages:
- **Latency:** p50 28 ms, max 72 ms (Latin script ~27 ms, Indic scripts ~44 ms).
- **Safety:** 0 false accepts with the §20.3 gate (≥ 0.85 and no correction).
- **Usefulness, out of the box: poor.** All 6 clear "yes" answers fell short of the gate (Hinglish 0.84, English 0.69, Kannada "ಹೌದು, ಬುಕ್ ಮಾಡಿ" 0.00, Malayalam 0.28), and `corrects_detail` was right in only 6/11 (Tamil and Telugu "yes" were classed as `phone`).
- So the gate would fail safe (always re-confirm) but add a turn to every booking. Phase 7 must measure it on the full labelled set, try question wording and Laya's calibration (`laya.calibrate`) and fine-tuning (`laya train`) tools, and fall back to `llm_fallback` per question where it misses its target.

### 23.3 How it fits

- `decisions/laya_client.py` implements the `DecisionModel` protocol: one `POST /v1/systemone` per turn carrying all questions from §20.2, returning `{answer, confidence, latency_ms}` per question, with `DECISION_TIMEOUT_MS`.
- Results are logged to `call_signals` with `model = laya-<checkpoint>@0.4.7`, so Laya and `llm_fallback` can be compared from the same table.
- Inside the agent, per-turn signals are fired without awaiting; the confirmation gate is awaited with the timeout inside `book_test_ride`, and fails safe to a re-confirmation.

### 23.4 Comparison plan (Phase 7)

Run Laya (`multilingual`, and `english` on English turns) and `llm_fallback` over the §20.5 labelled set.

| Metric | Measured per provider |
|---|---|
| Confirmation gate false accepts (target 0) | ✓ |
| Unnecessary re-confirmations (target ≤ 10 %) | ✓ |
| Intent accuracy per language | ✓ |
| Calibration (confidence vs. actual accuracy) | ✓ |
| p50 / p90 latency on the local GPU (and on CPU) | ✓ |

Enable `enforce` only for questions where Laya meets its target; others stay in `shadow` or use `llm_fallback`. Record the decision and numbers here.

---

## 24. Local infrastructure (Phases 10–11, future)

Phases 0–9 use **LiveKit Cloud** and the existing **hosted Supabase** project. Phase 10 moves LiveKit to a self-hosted server and Phase 11 moves the database to local Postgres, so the POC stops depending on those two cloud services. Both are config-only switches because the code only reads `LIVEKIT_URL`/keys and `DATABASE_URL`. Do them in either order.

Local env values for these phases:

```bash
LIVEKIT_URL=ws://localhost:7880          # Phase 10; dev keys below are local-only
LIVEKIT_API_KEY=devkey
LIVEKIT_API_SECRET=secret
POSTGRES_PASSWORD=<choose a local password>                                   # Phase 11
DATABASE_URL=postgresql://postgres:<same password>@localhost:5432/honda_voice
```

Phase 11 data move: `pg_dump --data-only` the bookings, leads and call_signals tables from Supabase and restore into local Postgres after migrations; re-seed the catalog from `data/catalog/`.

### 24.1 What still needs the internet

Self-hosting covers transport and storage only. These remain cloud APIs: **Sarvam** STT/TTS, the **Azure OpenAI** LLM, and **Google Calendar** (Laya already runs locally) (the calendar mirror can be switched off with `CALENDAR_SYNC=off`). A fully offline agent would need local STT, TTS and LLM models, which is out of scope.

### 24.2 `docker-compose.yml`

```yaml
services:
  livekit:
    image: livekit/livekit-server:<pin a version>
    command: --dev --bind 0.0.0.0        # dev keys: devkey / secret
    ports:
      - "7880:7880"        # signalling (ws)
      - "7881:7881"        # WebRTC over TCP fallback
      - "7882:7882/udp"    # WebRTC media (dev mode)
    restart: unless-stopped

  postgres:
    image: postgres:16
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: honda_voice
    ports: ["5432:5432"]
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./data/migrations:/docker-entrypoint-initdb.d:ro   # runs 001, 002, 003 on first start only
    restart: unless-stopped

volumes:
  pgdata:
```

Run `docker compose up -d livekit` (Phase 10) and/or `docker compose up -d postgres` (Phase 11), then re-seed with `uv run python scripts/seed_catalog.py`. To re-run migrations from scratch: `docker compose down -v && docker compose up -d`.

### 24.3 Why plain Postgres instead of the Supabase stack

The app uses only Postgres features (asyncpg, `pg_trgm`, plpgsql functions, RLS). It doesn't use Supabase Auth, Storage, Realtime or the auto-generated REST API. Plain `postgres:16` is one container instead of about ten. If you want the Supabase Studio UI, `supabase init && supabase start` (Supabase CLI, needs Docker) works with the same migrations; its Postgres listens on `localhost:54322`, user `postgres`, password `postgres`. Moving to hosted Supabase later only means changing `DATABASE_URL` to the session-pooler string.

RLS stays enabled in the migrations, so the schema is ready for hosted Supabase without changes.

### 24.4 LiveKit local gotchas

- **Dev keys** (`devkey` / `secret`) are fine on your laptop only. Before exposing the server on a network, generate real keys (`livekit-server generate-keys`) and run with a config file instead of `--dev`.
- **Same machine:** the browser at `http://localhost:3000` connecting to `ws://localhost:7880` works with no extra setup.
- **Another device on your Wi-Fi (e.g. a phone):** browsers only allow microphone access on `https://` pages or `localhost`. Serve the frontend over HTTPS (for example `mkcert` certificates, or `next dev --experimental-https`), point `NEXT_PUBLIC_LIVEKIT_URL` at your laptop's LAN IP, and make sure UDP 7882 isn't blocked by the firewall.
- **Agent worker:** connects to `ws://localhost:7880` with the same keys; explicit dispatch by `agent_name` works the same as on Cloud.
- **Console mode** (`... worker console`) needs neither LiveKit nor the frontend, so Phase 1 can start before Docker is set up.