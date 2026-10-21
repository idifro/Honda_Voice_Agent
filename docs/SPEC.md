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

**Out of scope (non-goals):** PSTN/SIP telephony, Indian languages beyond the six above, *calculating* on-road prices (published Delhi on-road prices are in scope, §6), real finance offers (demo EMI examples from code are in scope since Phase 7, §20.4), scraping the OEM website, multiple showrooms, an admin UI, and authentication beyond a shared demo passcode.

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
   Sales advisor (Phase 7): gpt-5.4-nano reads each turn in parallel (never delays audio) → caller
                            profile → one suggested move per turn (code) → scored lead at call end (§20)
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

### 4.2 Slow turns, stalls and failover (2026-10-16)

Added after a browser call hung: an Azure request stalled for the 10 s default before its retry, then the worker lost its LiveKit connection.
- **Streaming everywhere already:** STT interim transcripts, LLM started preemptively on the preflight transcript, token streaming, TTS from the first words, streaming `tts_node`. Typical turn: end-of-turn ~0.58 s, LLM TTFT 0.8–1.0 s, TTS first audio 0.2–0.3 s, TTFA ~1.5 s.
- **No second LLM call for fixed tool lines (Phase 6):** the read-back (`confirm_details`) and the booked line (`book_test_ride`) are text built in code, so on the step after those tools `BookingAgent._fixed_tool_reply` speaks the tool's `say` directly (only if no other tool in that step failed); before, a second LLM call just repeated it. Measured on the same eval scenarios: read-back turn 3.55 → 2.24 s, booked turn 2.54 → 1.27 s (medians). The booked line also got a closing question and a shorter SMS sentence.
- **Language decisions on discarded drafts (Phase 6):** preemptive generation can decide on a draft transcript that's then replaced; that draft's language switch is now undone (`CallState.turn_start_lang`), so one caller turn makes one decision. Switches are logged with their reason (explicit request, detected language, word count; no caller text). Prompted by 6 Tamil/English switches in the 2026-10-16 browser call, two within one second.
- **No LLM call when code knows the reply:** if the caller's first turn only picks a language ("Tamil", "English please": `language.is_language_pick`, and no model, price, competitor or out-of-scope words), `SalesAgent._fixed_reply` streams the intro line straight to TTS. Code replies only ever answer the caller's message itself, never a tool-reply step.
- **Stalls:** `LLM_ATTEMPT_TIMEOUT_S=4` (the HTTP stream gives up after 4 s without bytes; a streaming reply is never cut) and `LLM_MAX_RETRY=1` quick retries (0.3 s apart) instead of 10 s.
- **Failover:** with `AZURE_OPENAI_FALLBACK_DEPLOYMENT` (and optionally its own endpoint, key and model) set, `build_llm` returns LiveKit's `FallbackAdapter`: errors, 429 rate limits and stalls switch the call to the fallback; the primary is probed in the background and used again once healthy. Verified 2026-10-16 against Azure with a missing primary: the first request failed over in 1.08 s, the next went straight to the fallback (0.72 s). The model's quality must be checked with `evals.run_text_evals --deployment … --model …` before it's configured: `gpt-4.1-mini` misspoke prices in the Phase 1 benchmark. **Configured 2026-10-16 (product owner): `gpt-5.4-nano`** (deployment `voice-gpt-5.4-nano`) on the same Azure resource and key, so it covers deployment-level failures and rate limits (separate quota) but not a regional outage; a deployment in another region would. Eval with nano alone (2026-10-16): **41/45** (mini: 44–45/45). No price errors and no wrong bookings (the guard refused one booking after nano made up its own read-back, then it read back correctly); weaker at repeating fixed lines verbatim (said a booking code raw, "HND-W8X8D", so `tts_node` now spells any raw `HND-XXXXX` like `speak_reference`), one 10:30→10:00 slot slip, one 51-word reply; median first token 1.10 s (mini ~0.9 s). Fine as an emergency fallback, not as the primary.
- **Quota protection:** `MAX_CONCURRENT_CALLS=3` per worker process (LiveKit load function; at full load new calls go to another worker or wait), plus the frontend's sign-in and token rate limits (§12) and `MAX_CALL_MINUTES` (§16).
- **Chosen-time hint:** the LLM twice confirmed a neighbouring slot ("saade teen" 15:30 read back as 15:00). `[booking_state]` now shows "caller's chosen time": the requested time when it was free, then whatever was last read back; cleared when that slot is taken. A hint, not a refusal: a first version refused a different time once, and broke slot changes and slot-taken recovery (it called a just-taken slot "free"). The read-back remains the safeguard.
- **Hold line** (`agent/hold.py`): if the agent has been thinking for `HOLD_AFTER_S=2` (a slow tool or LLM), it says a short fixed line in the session language ("One moment please, let me check."; `prompts.HOLD_LINE`) and carries on; the reply follows when ready. It plays on a separate background audio track (`BackgroundAudioPlayer`), so it never enters the chat history or affects turn-taking, and once started it **finishes before the reply plays** (`tts_node` awaits `HoldAudio.before_reply`, at most 4 s; the reply's audio is synthesised meanwhile). Cutting it mid-sentence when the reply was ready sounded abrupt in the 2026-10-19 call. It stops at once if the caller speaks, and never starts if the reply is ready first. The line is synthesised by Sarvam once per language per call (prefetched at call start and on a language switch; 0.6–0.8 s, resampled 22.05 → 48 kHz). **Hold music was tried and removed** after the 2026-10-16 browser test (product owner: it didn't work well on the call).
- **Background voices** (`NOISE_CANCELLATION`, `worker.room_options`): LiveKit's BVC filter on the caller's audio removes other people's voices before STT and turn detection. In the 2026-10-19 call a distant speaker was transcribed as Hindi and switched the call's language. BVC is billed as LiveKit voice isolation (Build plan: 100 free minutes a month; paid plans $0.0012/min after their included minutes); `nc` (noise only) is free; `off` disables it. Requires LiveKit Cloud (Phase 10 must revisit it).
- **"10-digit" read letter by letter:** in the same test TTS said "1 0 d i g i t". The prompt and tool notes no longer mention the number of digits, and `tts_node` splits a digit-hyphen-letter token ("10-digit" → "10 digit") as a backstop.

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

### `002_booking_functions.sql` (applied 2026-10-15)

`data/migrations/002_booking_functions.sql` is the source of truth. Summary:
- **`reserve_slot(slot_id, name, phone, reference, room, language)`**: one `UPDATE slots SET booked = booked + 1 WHERE id = … AND booked < capacity AND starts_at > now()`, then the booking insert, in one transaction; raises `SLOT_UNAVAILABLE` if no row was updated. Concurrent callers block on the row lock and re-check the condition, so a slot can't be over-booked; a failed insert (same phone, same slot) undoes the increment.
- **`cancel_booking(reference, phone)`**: needs both to match a confirmed booking; frees the capacity.
- Both are `security invoker` with a fixed `search_path`; `EXECUTE` is revoked from `public`, `anon` and `authenticated`, so only the backend can call them (rule 9).
- `slots.capacity` defaults to **2 demo bikes per model per slot** (product owner, 2026-10-15).

`db/bookings.py` wraps them: `find_open_slots` (step 11's grid times filtered by real free capacity, nearest to the requested time; reasons `requested_time_full`, `fully_booked`, or the grid's own), `reserve` (reference `HND-XXXXX` from an alphabet without 0/O/1/I/L, spoken as "H N D, 7 K 3 Q 9"; errors `SLOT_UNAVAILABLE`, `DUPLICATE_BOOKING`), `cancel`, `capture_lead`, and `generate_slots` (used by `scripts/generate_slots.py --days 14`: on-sale models only, open days only, future times only; existing slots untouched). Generated on 2026-10-15: 6,931 slots (29 models × 14 days).

### `003_driving_licence.sql` (applied 2026-10-15)

A test ride needs a valid two-wheeler driving licence (product owner, 2026-10-15). `bookings.licence_confirmed boolean not null` with a check constraint (`NOT VALID`, so rows from before the migration, never asked, are kept) that rejects any new booking without a confirmed licence; `reserve_slot` gains `p_licence_confirmed`. The agent asks first (§8), so the database check is the backstop; `bookings.reserve` also refuses with `LICENCE_REQUIRED` before calling the database.

### `004_frontend_reader.sql` (applied 2026-10-15)

A least-privilege role for the Next.js server routes (rule 9). `frontend_reader` can read only two views in schema `frontend` (not exposed by Supabase's REST API): `frontend.bookings` (today and upcoming, **masked in SQL**: "Ravi K.", "90xxx xx345", plus model, slot, language, licence, calendar status) and `frontend.catalog` (models and base prices). It has no rights on the `public` tables or functions, every transaction is read-only, statements time out after 3 s, and it may hold at most 3 connections. The migration creates it without a password; `scripts/set_frontend_db_password.py` sets a random one (only a SCRAM-SHA-256 verifier is sent to Postgres) and writes the role's URL to `frontend/.env.local`; re-run it to rotate. `tests/integration/test_frontend_reader.py` checks the masking and every refusal.

### `005_advisor_leads.sql` (applied 2026-10-20)

The sales-advisor and lead tables of §20.6:
- **Advisor data:** `bike_fit`, `finance_schemes`.
- **Calls and leads:** `customers`, `calls`, `caller_profiles`, `call_signals`.
- **Extended:** `leads` (customer, call, score, status, next action, callback time, interest model) and `bookings` (customer, call).

Constraints and access:
- RLS is on for every new table.
- Check constraints cover the fit vocabulary, lead score and status, and callback consent: a consent needs its time and text version.
- `customers.phone` is unique.
- The frontend role gets two more masked views, `frontend.leads` and `frontend.caller_profiles`, and nothing in `public`. Integration tests check the masking and the refusals.

**Concurrency test (Phase 3 exit criterion):** the Supabase session pooler caps clients at 15, so `tests/integration/test_bookings.py` opens 14 connections and fires 20 reservations on a capacity-1 slot at once (14 simultaneous, 6 queued): exactly 1 succeeds, 19 get `SLOT_UNAVAILABLE`. A deterministic companion test holds the row lock in one transaction and shows the second caller waits, then fails if the first commits and succeeds if it rolls back.

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

**Catalog completion (2026-10-20, Phase 7 step 1, §20.7), `data/catalog/_enrichment.json`:** sourced values applied by the seed script *before* the price overrides, so product-owner decisions still win.
- **Sources:** BikeDekho's New Delhi price and specification pages, chosen because every on-road row's parts add up to its total and its variant prices matched ZigWheels and BikeWale's Delhi figures.
- **Variant prices:** 25 of 33 models fully priced. A model priced from one source gets its range from those prices, which refreshed stale ranges, e.g. XL750 Transalp ₹11.81 L → ₹13.20 L, SP125 ₹89,280 → ₹90,498. The seed report lists every change.
- **On-road:** rows for 28 models replaced with complete breakdowns. The electric scooters keep BikeWale's rows, which subtract the PM E-Drive subsidy (still in force, extended to March 2028); BikeDekho's don't.
- **Mileage:** filled for 17 models with the source's label ("BikeDekho overall", "ARAI").
- **Colours and features:** filled for every model that had none.
- **Weight and seat height:** filled for `bike_fit`.
- **Estimates:** mileage for 9 big bikes with no published figure, plus 6 size values, including the Rebel 300's 171 kg because BikeDekho repeats the Rebel 500's weight. They're added to `unverified_fields` or flagged `is_estimate`: used for ranking, never spoken.
- **Product-owner decisions (2026-10-20):**
  - CB350C and CB350RS: DLX Pro is the higher price, so BikeDekho's cheaper "STD" is our DLX.
  - Rebel 500: on sale, at HMSI's launch price of ₹5.12 lakh, ex-showroom Gurugram (the only official figure, so the price city is Gurugram). It's sold in Gurugram, Mumbai and Bengaluru; the E-Clutch variant isn't launched and has no price.
  - The editorial `bike_fit` tags are approved.
- **Left empty (in `left_empty`, shown in the seed report):**
  - SP125 Anniversary Edition: not listed by any Delhi source.
  - Rebel 500: E-Clutch price, and on-road (no Delhi price).
  - Activa e RoadSync Duo on-road breakdown: sources about ₹9,000 apart.
- **Correctly empty and left so:** cc on electric scooters, range on petrol bikes, notes, booking calendar errors that never happened.
- **SP160:** the override was extended with Double Disc at ₹1,23,570, now listed.
- **`_finance_schemes.json`:** 3 demo EMI plans (`is_demo`). EMI is computed by `domain/finance.py`, whose tests check it against published loan figures.

**Phase 2 alias and matching notes (from step 7):** add the misspellings STT actually produced: `hiness`, `hince`, `cb350hince`, `cb350 hiness`, `highness` (and keep collecting them from logs). Matching must use **longest match wins**: "Activa 125" also hits the bare alias `activa` (Activa 110) and "Shine 100" hits Shine 125's `shine`, so a shorter alias contained in a longer matched alias is dropped. Keep the fuzzy/trigram threshold tight: at 0.8 similarity, "CB350 Hiness" also matched CB350C, CB500 and CB750; ambiguous fuzzy results go back to the caller as "which one?".

**Step 10 (2026-10-15): the agent reads the database.** The worker loads the whole catalog (~0.4 s) into a `Catalog` snapshot in the background while the greeting plays, so alias matching, pre-fetching, `list_bikes` and `compare_bikes` cost no database round trip mid-turn; only the trigram fallback queries Postgres (~85 ms from India). Catalog changes take effect on the next call. Results on the step 7 recordings with the live lookup (`codemix`): every expected model found in 9/9 clips (100 %); exactly the expected set in 8/9 (89 %), the difference being a clip where the caller also said "CB350" as the family name. Text-mode check: 15/15, including on-road, list, compare, a Tamil on-road breakdown and a bare "activa" (agent asks which Activa).

**Aliases in every script:** each model gets aliases in Latin script plus Devanagari, Tamil, Telugu, Malayalam and Kannada spellings (e.g. `ஆக்டிவா`, `యాక్టివా`, `ആക്ടിവ`, `ಆಕ್ಟಿವಾ`), because `codemix` usually but not always keeps model names in Latin script. Phase 1 recordings add the misspellings STT actually produces.

## 7. Tools

All tools are `@function_tool` methods with pydantic-typed arguments. Each returns a small dict meant for the LLM to speak from, never raw rows. Spoken strings (`spoken_price`, slot times) are rendered in the **session language** (§4.1).

### SalesAgent

| Tool | Args | Behavior |
|---|---|---|
| `find_bike` | `query: str` | `db/catalog.py` (step 10). **Alias match, longest wins** (`domain/alias_match.py`: "Activa 125" doesn't also hit Activa 110's bare "activa"); if an alias covers only part of the query, a clearly better **trigram** match wins ("cb350 hinees" → H'ness); otherwise trigram fallback (similarity ≥ 0.4, top must beat the runner-up by 0.1, else up to 3 candidates to ask "which one?"). A bare family name ("activa", "hornet") also returns `similar_models` so the agent asks which one. Returns `domain/catalog_view.bike_summary()`: `{found, bike_id, name, segment, status, engine_cc, mileage_kmpl, specs{every verified spec}, features?, colours?, pitch, price_available, price_min_spoken, price_max_spoken?, price_note, variants:[{variant_name, spoken_price\|null}], on_road{available, city, from_spoken, cheapest_variant, variants[], breakdown{ex_showroom, rto, insurance, other[] (deductions marked)}, note}, similar_models?, booking_note?}`. Rows flagged `needs_review` never appear. When nothing matches and the caller named a competitor model, **the pivot is done in code** (`domain/competitors.py`, step 14): `closest_honda` carries that Honda's facts (Classic 350 → CB350 H'ness, Jupiter → Activa 125, Pulsar → SP160 …) with a note to say nothing about the other product; a brand alone gets "ask segment or budget"; anything else gets a callback offer. Competitor turns are also pre-fetched this way. |
| `check_showroom_open` | `day_ref, time_24h?` | `day_ref` ∈ `today, tomorrow, day_after, mon…sun, YYYY-MM-DD`. Resolved in code (`datetime_resolve.showroom_status`, rule 5) against the showroom's open/close times and closed weekdays. Returns `{date, weekday, is_today, open_that_day, opens, closes, open_now?, time_asked?, open_at_time_asked?, note?}`; the LLM says the times naturally in the session language. |
| `list_bikes` | `segment?: str, max_price_inr?: int` | Up to 4 models, cheapest first (one of the 17 segments; budget as a plain rupee number), each with `ex_showroom_from_spoken` and `on_road_from_spoken`. |
| `compare_bikes` | `a: str, b: str` | Both resolved like `find_bike` (Honda only; unresolved → ask which). Returns key fields side by side: segment, engine, mileage, ex-showroom range, on-road from, and verified key specs. |
| `start_booking` | `model?, day_ref?, time_24h?` | Hands off to `BookingAgent` with chat context (step 13). The model is resolved by `Catalog.find_one`: a model's own name settles it even when a longer one contains it ("Shine 100" vs "Shine 100 DX"), a bare family name ("activa") leaves it unset so BookingAgent asks. If the caller already gave a day, **BookingAgent runs the slot search in code on entry** and replies from it (step 14: the LLM once claimed "6 baje theek rahega" without checking; this also saves one LLM call). Pre-booking and upcoming models aren't handed over: the note says to offer a callback lead. |
| `capture_lead` | `reason, name?, phone?, interest?` | On both agents (shared base). Fallback path; writes to `leads` in the session language. An invalid phone is refused unless the phone tries are used up. |

**Shared call state (step 13).** A handoff creates a new Agent object, so the session language, STT detection, caller turn count, chosen model and booking draft live in `agent/call_state.CallState`, shared by every agent of the call. `agent/base_agent.MultilingualAgent` holds what both agents do each turn: the language decision and tag in `llm_node`, TTS retargeting and streaming cleanup in `tts_node`. The receiving agent replies on entry (`generate_reply`), because a bare handoff tool doesn't reply itself.

### BookingAgent

| Tool | Args | Behavior |
|---|---|---|
| `check_slots` | `day_ref, time_24h?, model?` | `day_ref` ∈ `today, tomorrow, day_after, mon…sun, YYYY-MM-DD`; `model` defaults to the one chosen. Resolves via `datetime_resolve` (§10) and `bookings.find_open_slots` (real free capacity). Returns `{model, slots:[{spoken, day_ref, time_24h}], exact_time_available, reason?, note?}`, up to 3 slots spoken in the session language ("kal shaam 5 baje", "நாளை மாலை 5 மணிக்கு"). Every returned slot is recorded as offered. |
| `record_licence` | `has_valid_licence: bool` | The caller's answer to the licence question (migration 003). Refused in the caller turn the booking started (the question can't have been answered yet: step 14 eval). A "no" ends the booking with a callback offer (`capture_lead`, reason `no_licence`). |
| `validate_phone` | `digits: str` | The caller's words unchanged (the LLM must not convert number words: it dropped a digit once). `phone.normalize` (strips +91/0/spaces, number words, native digits), validates `^[6-9]\d{9}$`, records the number as validated. Input with no digits or number words (e.g. the caller's name) isn't counted as a try. Two failures in a row: the note says to offer a callback lead. |
| `confirm_details` | `day_ref, time_24h, name, phone` | Slots are named by day and time, not ids (step 14: the LLM mixed up opaque slot ids); code maps them to the slot offered for the chosen model. Builds the single read-back summary in code (`prompts.CONFIRM_SUMMARY`: model, spoken slot, name, phone digit by digit grouped 5+5, "9 8 7 6 5, 4 3 2 1 0", and "with a valid driving licence") and records it as pending. Refused unless the slot was offered, the licence confirmed and the phone validated. The LLM says `say` verbatim. |
| `book_test_ride` | `day_ref, time_24h, name, phone, caller_confirmed: bool` | **Guarded in code (`domain/booking_guard.py`, rule 6):** refused unless `caller_confirmed` is true, the licence is confirmed, a summary was read back, **the yes comes in the very next caller turn after it** (not the same reply, and not after anything else was said: step 14 found the LLM saying "changed to 11:30" without a new read-back, then booking 11:00 on the caller's yes), and slot, name (case/spacing-insensitive) and normalized phone equal the read-back exactly. Caller turns are counted once each: a preemptive draft whose transcript changed doesn't count as a turn. Calls `reserve_slot` (with the session language and room). On success returns `say` (`prompts.BOOKED_LINE`, with the reference spelled "H N D, 7 K 3 Q 9"); the calendar mirror task is added in Phase 4. On `SLOT_UNAVAILABLE`, clears the read-back and returns the 2 nearest open slots on that day; `DUPLICATE_BOOKING` is reported as such. |
| `cancel_test_ride` | `reference, phone` | `bookings.normalize_reference` accepts "h n d 7 k 3 q 9", "7K3Q9", "HND-7K3Q9"; calls `cancel_booking` (needs the phone that booked). |
| `capture_lead` | (shared, above) | |
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

**Competitor policy:** if a caller mentions another brand, acknowledge it neutrally, make **no claims** about the other product, and pivot to the closest Honda model with facts from `find_bike`. The closest Honda is picked in code (`domain/competitors.py`), not by the LLM. Example (Hinglish; `prompts.py` has one per language): "Achha, aap scooter dekh rahe hain — Honda mein Activa 125 is segment mein hai, uska mileage aur price bataun?" Do not refuse or lecture.

**Out of scope** (servicing and repairs, insurance renewal or claims, unrelated topics; EMI left this list in Phase 7, §20.4): one-line deflection plus offer a callback lead. `domain/scope.py` detects these topics in code and skips the model pre-fetch for that turn (step 14: "when should my Activa be serviced?" got pre-fetched Activa facts and an "ask which Activa" note, and the LLM answered). Plain "insurance" stays in scope: it is part of the on-road breakdown.

**Comparisons:** at most two differences; prices only when asked. When two models are pre-fetched and the turn has no price word (`scope.asks_about_price`), their price fields are left out of the facts (step 14: comparisons kept adding unasked prices, 55+ words).

**Booking script** (BookingAgent), one field at a time:
1. Model (pre-filled if known), then day and time, then the slot search: if the requested time is free, say so; otherwise give the reason and offer up to 3 slots.
2. **Driving licence** (added 2026-10-15): "Do you have a valid two-wheeler driving licence?", then `record_licence`. No licence: no booking, offer a callback. Once per call.
3. Name; the agent thanks the caller by name when asking for the phone.
4. Phone, then `validate_phone`. Re-ask digit by digit on failure; after 2 failed tries, offer `capture_lead`. A valid number isn't read back separately: the summary in step 5 reads it back grouped 5+5 (one turn fewer, step 13).
5. A single confirmation summary built in code by `confirm_details`, in the session language ("Honda Activa 125, kal shaam 5 baje, naam Ravi, number 9 8 7 6 5, 4 3 2 1 0, valid driving licence ke saath. Kya main yeh test ride confirm kar doon?"). `book_test_ride(caller_confirmed=True)` only after an explicit yes, enforced by `booking_guard` (§7). A correction ("make it 11:30") means a new summary.
6. Speak the reference code slowly, remind them to bring the licence, and say an SMS would normally follow, not in this demo (`prompts.BOOKED_LINE`).

Time words (`prompts.TIME_WORDS`, in both agents' prompts) include Hindi half and quarter hours: "saade X" = X:30, "sawa X" = X:15, "paune X" = (X−1):45, "dedh" = 1:30, "dhaai" = 2:30 (step 14: "subah saade das" became 09:30 when only the Sales prompt had the time words).

**Verified 2026-10-15 (step 13, text mode, real LLM + DB; now part of `evals/run_text_evals.py`, §15):** 5/5 scenarios: English and Tamil happy paths, a too-short phone number then a slot change at the read-back, the slot taken between read-back and yes (alternatives offered, new read-back, booked), and cancellation by spoken reference. Every booking row matched the read-back. Eval bookings are cancelled and deleted afterwards.

## 9. Speech text transforms (`agent/text_transforms.py`)

Applied in an overridden `tts_node` as **streaming** transforms (chunk-in, chunk-out; carry a small buffer only across token boundaries).

1. Strip markdown (`*`, `#`, bullets), emojis, and URLs.
2. Backstop for currency: if a raw `₹` amount slips through, rewrite it with `format_inr_spoken(amount, session_language)`. Count it as a guardrail violation in metrics, because the prompt should have prevented it.
3. Expand "km/l" → "kilometre per litre", "cc" → "C C".
4. No truncation; over-length turns are only logged.
5. Drop echoed internal lines: if the LLM repeats `[caller_language] …` or `[booking_state] …`, the tag and the rest of that line are removed and counted as a guardrail violation. The same filter runs on the LLM stream in `llm_node` (`InternalTagFilter`), so an echoed tag never reaches the chat history: in step 14 one echo was copied on every later turn of the call. The `tts_node` filter stays as the backstop.

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

`datetime_resolve.candidate_slots(day_ref, time_24h, now, showroom)` (step 11) resolves the day, drops slots starting within the 30-minute lead time, and returns up to 3 grid times nearest the request (or the first 3 of the day) with `exact` and a `reason` when the request couldn't be met as asked: `closed_day`, `outside_hours`, `not_on_slot_grid`, `too_soon_or_past`, `no_slots_left_today`. The grid runs every `slot_minutes` from opening, each slot ending by closing (demo: 10:00–18:30 starts). Capacity comes from the `slots` table (step 12).

`datetime_resolve.speak_slot(dt, now, lang) -> str` says a slot relative to today in the session language: today/tomorrow/day-after words, a weekday name within the week, weekday + date beyond it; morning/afternoon/evening words; Hindi half hours as "saade 10", "dedh", "dhaai". Examples: "kal shaam 5 baje", "tomorrow at 5 pm", "நாளை மாலை 5 மணிக்கு", "రేపు సాయంత్రం 5 గంటలకు", "നാളെ വൈകുന്നേരം 5 മണിക്ക്", "ನಾಳೆ ಸಂಜೆ 5 ಗಂಟೆಗೆ". **Verified (2026-10-15)** by a TTS→STT round trip in all six languages. Half hours in Tamil, Telugu, Malayalam and Kannada are written as hour + minutes ("10 மணி 30 நிமிடத்துக்கு"), because TTS read "10:30 மணிக்கு" as "ten o'clock thirty minutes o'clock". Native-language words need a native-speaker review.

`phone.normalize(raw) -> str | None` and `phone.readback(p) -> "98765 43210"`. Digits are language-neutral; TTS reads them in the session language. `normalize` also accepts native-script digits (e.g. Devanagari `९८७६५…`).

## 11. Google Calendar mirror

- Create a dedicated calendar ("Honda Demo – Test Rides") in a normal Google account, and share it with the service account email with "Make changes to events". Put its ID in `GOOGLE_CALENDAR_ID`. **Do not use `primary`**; that is the service account's own invisible calendar.
- Do not add attendees (that requires domain-wide delegation). Put customer details in the event description.
- After `reserve_slot` commits, a background task creates the event (3 retries with backoff) and updates `calendar_event_id` / `calendar_sync_status`. A failure sets `FAILED` and is never spoken to the caller. `scripts/retry_calendar_sync.py` re-pushes `PENDING`/`FAILED` rows. Cancellation deletes the event the same way.

**Built (Phase 4, 2026-10-15):**
- `integrations/gcalendar.py`: async REST client (aiohttp) for Calendar API v3 `events.insert/get/delete`; the service-account token is signed by `google-auth` (pinned 2.43.0, the only new dependency) and refreshed in a thread about hourly. 429/5xx/network errors are retryable; other 4xx (e.g. 403 not shared) fail at once.
- `domain/calendar_event.py`: the event for a booking. **Its id is derived from the booking reference** (base32hex), so a retried insert gets 409 and counts as done: no duplicate events. Summary "Test ride: Honda Activa 125 (Ravi)", IST start/end (slot length from the showroom), location, and a description with booking code, model, name, language and licence. **The phone is masked** (`xxxxxxx210`; full number only in the database), per §17 data minimisation; change `build_event` if showroom staff need it in the calendar.
- `db/calendar_sync.CalendarSync`: `schedule(booking_id)` runs the sync in the background (the caller's turn never waits for Google); one sync at a time per booking; a confirmed booking gets an event, a cancelled one loses it. Status writes are conditional on the booking's status at that moment, so a cancel that races an in-flight insert ends with the event deleted. `drain()` runs at call end (job shutdown callback); anything unfinished stays `PENDING`.
- Cancelling marks the row `PENDING` (`bookings.mark_calendar_pending`) and schedules the delete. With `CALENDAR_SYNC=off` nothing is mirrored and rows stay `PENDING` for the retry script.
- `scripts/check_calendar.py` checks the setup (create, read back, delete a test event); `evals/calendar_e2e.py` is the Phase 4 exit check against the real calendar.
- Setup: a Google Cloud project with the Calendar API enabled (free, no billing account), a service account with a JSON key in `secrets/` (git-ignored, `chmod 600`), and a calendar in a normal Google account shared with the service account's email with **Make changes to events** ("See all event details" gives 403 "You need to have writer access").
- No free/busy check. Slot availability comes only from the `slots` table.

## 12. Frontend (`frontend/`)

- Start from LiveKit's **agent-starter-react** template (MIT; commit `c5d78a6`, 2026-09-15, its lockfile kept, Next.js 15.5). It provides the visualizer, control bar, transcription, and connection handling. Branding and panels are ours; the template's sandbox-token and LiveKit-homepage-agent fallbacks were removed.
- **Sign-in:** `POST /api/login {passcode}` checks `DEMO_PASSCODE` in constant time (rate limit: 5 a minute per IP) and sets `hv_session`: an HS256 JWT signed with `SESSION_SECRET`, 2 h, `HttpOnly`, `SameSite=Strict`, `Secure` in production. The page renders the passcode screen (with the privacy notice) until the cookie is valid; `POST /api/logout` clears it. Every API route below requires the cookie.
- `app/api/token/route.ts` (the template's path): replaces the template route, which had no authentication and let the browser choose the room configuration, i.e. which agent is dispatched. **The request body is ignored**; the server picks a random room (`honda-<uuid>`) and identity, grants join + subscribe + **microphone only**, no data publishing, no admin, a **10-minute** token, and a room configuration that **explicitly dispatches** `AGENT_NAME` (`honda-sales`). Rate limit: 10 a minute per IP.
- `app/api/bookings/route.ts`: reads `frontend.bookings` (already masked by the database) through the `frontend_reader` role; the panel polls every 5 s (paused in a background tab). `app/api/catalog/route.ts`: models and ex-showroom prices from `frontend.catalog`.
- **Database TLS:** node-postgres connected in cleartext by default (found in Phase 5); `lib/server/db.ts` uses TLS with Supabase's root CA (`certs/`, via `DATABASE_CA_CERT`), verifying the chain and host name.
- **Headers:** `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy: microphone=(self), camera=(), geolocation=()`, no `X-Powered-By`.
- Voice only: the caller can't send text or data (chat input off); the transcript is always shown (one change in the template's session view). Calls end after `MAX_CALL_MINUTES` (worker, §16).
- Transcripts come from the agent session's built-in transcription; no custom data channel is needed.
- Tests: `cd frontend && corepack pnpm@9.15.9 test` (Vitest): passcode, session tampering, rate limits, the token's claims (including that a hostile body can't change room, identity or agent), and sign-in on every data route. Verified against the real services on 2026-10-15 with a production build; the client bundle contains none of the server secrets.

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

# Sales advisor (Phase 7, §20): caller signals from gpt-5.4-nano (the fallback deployment); Laya optional (§23)
ADVISOR=on                       # on | shadow (signals logged, no coach line) | off
SIGNAL_PROVIDER=nano             # nano | laya
SIGNAL_DEPLOYMENT=voice-gpt-5.4-nano
LAYA_BASE_URL=http://127.0.0.1:8100   # only with SIGNAL_PROVIDER=laya: local laya-serve
LAYA_MODEL=multilingual

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
**Status (2026-10-15): exit criteria met.** Concurrency test passes (§5); `evals/run_text_evals.py` passes all booking, phone, slot-taken and cancellation scenarios, 45/45 overall on the final clean runs (§15). Added beyond the plan: driving-licence question (migration 003). Still open from Phase 1: a voice confirmation of the language-switch fix. The native-speaker review of the Tamil, Telugu, Malayalam and Kannada lines was waived for the POC by the product owner (2026-10-16: "fine for now").

**Phase 4 — Calendar mirror.** Shared calendar, background sync, retry script.
*Exit:* a booking appears in the shared calendar within 10 s, and a simulated calendar outage leaves the booking confirmed with status `FAILED` until the retry script fixes it.
**Status (2026-10-15): exit criteria met** (`evals/calendar_e2e.py`, real calendar): the event was readable 1.7 s after the booking committed; with the calendar unreachable the booking stayed `CONFIRMED` with `FAILED`, and `scripts/retry_calendar_sync.py` then synced it; cancelling removed the event. Integration tests with a fake calendar cover retries (4 tries), no retry on 403, cancel, and cancel racing an insert.

**Phase 5 — Frontend.** Starter template, token dispatch with passcode, bookings and catalog panels.
*Exit:* a full call from the browser books a ride that appears in the panel within 5 s.
**Status (2026-10-15): built and tested; the browser call (exit) is pending the product owner's test.** Added at the product owner's request: rate limits on sign-in and tokens, a maximum call length, the panels behind the sign-in cookie, and the read-only masked database role (migration 004). Found and fixed on the way: node-postgres connected to Supabase in cleartext, and the Python backend encrypted without verifying the certificate; both now verify against Supabase's root CA (`certs/`).

**Phase 6 — Evals and hardening.** The full eval suite, a latency report, and a demo script.
*Exit:* all §1 targets met; README with setup steps.
**Status (2026-10-16): built; exit pending five live demo runs (§1 says targets are met on the eval set and in five live runs).** Delivered: `scripts/latency_report.py`, `scripts/check_all.sh` (full run: all tests pass, conversations 44/46 = 96 %), `README.md` (setup from scratch, privacy notice), [DEMO.md](DEMO.md) + `scenarios/demo.yaml` (passed 3/3 after fixes), the fixed-tool-reply speed-up, and two measurement fixes (turn records, discarded-draft language decisions). Where each §1 target stands:

| §1 target | Evidence so far | Status |
|---|---|---|
| TTFA p50 ≤ 2.0 s / p90 ≤ 3.0 s | 2 browser calls before the speed-up: p50 1.65 s, p90 4.26 s (booking turns; one Azure stall of 25.7 s). Booking turns since cut by ~1.3 s each (read-back 3.55 → 2.24 s, booked 2.54 → 1.27 s in evals) | p50 met; p90 to re-measure on the live runs |
| Model names from speech ≥ 90 % (≥ 80 % per language) | Phase 2: 9/9 on the recorded English + Tamil clips | Met for English and Tamil; Hindi, Telugu, Malayalam, Kannada recordings not available |
| Reply language matches ≥ 95 % | Evals check script on every turn: 1 slip in 46 conversations (one Kannada word in a Malayalam reply). Live figure (69 %) was a filing bug in the turn records, fixed | Evals met; live to re-measure |
| Scripted bookings completed ≥ 80 % | All booking, phone, slot-taken, Indian-language and demo bookings passed in the final runs | Met |
| Wrong phone/date/model persisted: 0 | Every eval booking row is checked against what the caller agreed; code guard on every booking | Met (0) |
| Spoken prices differing from catalog: 0 | Price check on every eval turn (tool output or pre-fetched facts) | Met (0) |
| Turns over ~40 words ≤ 10 % | Evals: a few 41–50-word warnings per run; live 2/17 = 12 % before the booked line was shortened | To re-measure on the live runs |

Known LLM variance (not fixed, reported): occasional 41–50-word replies, rare neighbouring-slot confusion (the read-back exposes it), rare script slips in Indian languages.

**Phase 7 — Sales advisor and leads (§20).** Redesigned 2026-10-19 (product owner): caller understanding on gpt-5.4-nano instead of Laya (§23.2b), no booking-confirmation gate, one advisor-style SalesAgent. Steps:
0. **Done 2026-10-20.** Two fixes from the 2026-10-19 live call: background-voice cancellation (`NOISE_CANCELLATION`, LiveKit BVC) and a hold line that finishes before the reply instead of being cut.
1. Data: catalog completion (§20.7), `bike_fit`, demo `finance_schemes`, migration 005, masked frontend views. **Done 2026-10-20** (§5, §6).
2. Understanding: signal provider (nano; Laya optional), profile merge, `call_signals`, `caller_profiles`, labelled set. **Built 2026-10-20** (§20.9); live latency check pending.
3. Advisor: `recommend_bikes`, `emi_examples`, next-move rules, coach line, new intro, scenarios. **Built 2026-10-20** (§20.10).
4. Leads: consent, lead score, call summary, `customers`, Leads and Caller-profile panels, CSV export, privacy notice. **Built 2026-10-20** (§20.11).
5. Optional, later: train Laya on nano's labels; switch provider only if it matches nano.
*Exit:* §20.8 targets met; all existing scenarios pass; p50 TTFA unchanged (±50 ms) vs Phase 6.

**Phase 8 — LangGraph orchestration, A/B (§21).** Build the graph behind `ORCHESTRATOR=langgraph` without removing the native path. Run the latency A/B across three router configs.
*Exit:* latency report comparing configs in §21.3; decision recorded here (adopt / adopt with decision-model router / stay native) with reasons.

**Phase 9 — Optional FastAPI service (§22).** Only if a trigger in §22.1 applies.
*Exit:* frontend works with `API_BASE_URL` set and with it empty; Next.js no longer holds `DATABASE_URL` when the API is enabled; API tests pass.

**Phase 10 — Self-hosted LiveKit (§24).** Replace LiveKit Cloud with the open-source server in Docker. Only `LIVEKIT_URL` / keys change; no code changes.
*Exit:* full browser call and booking work against `ws://localhost:7880`; latency report re-run and compared with Cloud.

**Phase 11 — Local Postgres (§24).** Replace hosted Supabase with `postgres:16` in Docker, migrations applied on first start, catalog re-seeded. Only `DATABASE_URL` changes.
*Exit:* all integration tests and evals pass against local Postgres; data export/import path from Supabase documented.

Jev (TypeSafe AI, paid) was dropped on 2026-10-15 in favour of Laya (§23); Laya in turn was replaced by gpt-5.4-nano for caller understanding on 2026-10-19 (§23.2b) and stays an optional provider.

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
- `test_calendar_sync.py`: fake calendar against the real database: sync, outage then retry, permanent error not retried, cancel, cancel racing an insert. `tests/test_gcalendar_client.py` covers the client's status handling offline.

**Conversation evals (`evals/`):**
- `scenarios/*.yaml` (step 14): **45 scripted text conversations** in 11 files: price ×6, comparison ×3, competitor ×3, booking happy path ×5, misheard/invalid phone ×3, slot taken ×2, cancellation ×2, out of scope ×3, language (intro, switches, explicit request) ×4, Tamil/Telugu/Malayalam/Kannada price + booking ×8, other paths ×6 (hours, budget list, one spec, upcoming model, no licence, price question mid-booking). Each turn gives the caller's words and the STT language; expectations are per turn (`tools`, `no_tools`, `slots_checked`, `time_24h` (the time the LLM extracted; added 2026-10-20 after "shaam saade chaar" came out as 15:30/15:45), `says_any/all/none`, `price`, `language`, `intro`, `honda_pivot`, `max_words`) and per scenario (`said_any` (any reply in the call; added in Phase 7), `booked`, `cancelled`, `book_errors`, `leads`, and the stored booking row: model, time, name, phone, language, licence). Hooks simulate other callers taking the slot; cancellations use a booking made before the call.
- `scenarios/demo.yaml` (Phase 6): the live demo script ([DEMO.md](DEMO.md)) as one conversation, so it's re-proven by every pre-demo check. 46 scenarios in total.
- **Pre-demo check:** `scripts/check_all.sh` runs lint, unit, database and frontend tests and the conversations (`--min-pass 0.95`: LLM variance), and prints a pass/fail summary; `SKIP_EVALS=1` for the quick version.
- `run_text_evals.py` runs them against the real LLM and database through `session.run()`, with no audio, and checks every turn for: reply language and script, ≤ 40 words (41–50 is a warning; a scenario can allow more, e.g. a full on-road breakdown), prices verbatim from tool output (a Tamil case ending on the rupee word is accepted), echoed tags, booking only on a turn where the caller said yes, licence recorded only after a yes. Bookings use room `eval-text` and are cancelled and deleted afterwards; leads with eval numbers (90000xxxxx) too. Flags: `--only`, `--tag`, `--deployment/--model`. It replaces the Phase 1 and step 13 one-off scripts.
- **Results (2026-10-15, `gpt-5.4-mini`):** the first run passed 34/42 and exposed real defects, fixed in code where possible (handoff slot search, slot ids → day/time, read-back must directly precede the yes, licence turn rule, competitor pivots, out-of-scope detection, tag filter in `llm_node`, time words in both prompts, script rule per language, comparison prices). Final five runs: **45/45, 41/45 (before the last fixes), 45/45, 44/45, 44/45**. The last two failures were the guard doing its job (a booking refused because the LLM skipped a new read-back; a licence "yes" attempted on the handoff turn and refused); the scenario and the check were adjusted, not the guard. First-LLM-call TTFT p50 ≈ 0.8 s, p90 ≈ 1.0 s. Residual LLM variance: replies of 41–50 words appear in a few turns per run.
- `evals/recordings/`: 30 audio clips across the six languages for the STT and model-name check (git-ignored; share them separately).

## 16. Observability

- Per turn, log: room, agent, detected language + probability, session language (and switches), STT final latency, LLM TTFT, TTS TTFB, end-to-end TTFA, tool calls with duration, word count, and guardrail violations (including reply-script mismatches).
- `scripts/latency_report.py` (Phase 6) reports TTFA p50 / p90 / max overall, by turn type (reply from code, LLM without tools, catalog tool, booking/handoff) and by reply language, with end-of-turn, LLM first token and TTS first byte, plus turns without a reply, over-length replies, reply-language match and guardrail violations, each against the §1 targets. Filters: `--since`, `--room honda-` (browser calls), `--last N`; `--md` writes a file.
- **Turn records (fixed in Phase 6):** a reply's language and words are now filed under the turn in which the reply *started* (before, a reply that ended after the caller's next turn began was filed under the next turn, which made the 2026-10-16 call show 69 % reply-language match). A turn's line is written once its reply has finished, and carries an `at` timestamp.
- Mask phone numbers in logs (last 3 digits only).
- **During development the worker writes its INFO log to `logs/worker.log`** (`LOG_FILE`; JSON lines with room and job ids, 10 MB x 5, git-ignored), so a problem call can be read back in full. DEBUG isn't written: LiveKit logs tool arguments, which can hold phone numbers, at DEBUG. Set `LOG_FILE=` empty when development is complete.
- **Call length:** `MAX_CALL_MINUTES=10`: the agent says a goodbye line in the session language and closes the room (cost limit).

## 17. Privacy

- There is no spoken recording-consent step (removed 2026-10-15 at the product owner's request), and agent sessions are not recorded (`record=False`). Name and phone are personal data under India's DPDP Act: the frontend shows a privacy notice on the sign-in screen and before a call (what is processed live and by whom, what a booking stores, the masked calendar copy). **Still open:** the same notice in the README.
- Collect only name, phone, preferred slot and the licence answer. Mask phone numbers in logs, the calendar and the UI; the frontend only ever receives masked values (the `frontend_reader` role can't read the raw columns).
- **Data in transit:** browser audio is encrypted by WebRTC (DTLS-SRTP), the API runs over HTTPS in production, and both the worker and the Next.js server verify the database's TLS certificate against Supabase's root CA (`certs/supabase-prod-ca-2021.crt`, `DATABASE_CA_CERT`).
- **Processors:** Sarvam (audio), Azure OpenAI (transcripts; content filter on), Google Calendar (masked booking copy), LiveKit Cloud (media relay), Supabase (database).
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
├── services/laya/run.sh        # optional local Laya server (§23)
├── data/
│   ├── catalog/                # one JSON per model + _index, _enrichment (sourced fill), _price_overrides,
│   │                           #   _on_road_delhi, _stt_aliases, _finance_schemes (demo EMI)
│   └── migrations/
│       ├── 001_init.sql
│       ├── 002_booking_functions.sql
│       ├── 003_driving_licence.sql
│       ├── 004_frontend_reader.sql
│       └── 005_advisor_leads.sql
├── scripts/
│   ├── build_catalog.py        # regenerates data/catalog/
│   ├── apply_migrations.py     # applies data/migrations in order, records them
│   ├── seed_catalog.py         # validate + upsert the catalog (wraps db/catalog_seed.py)
│   ├── generate_slots.py
│   ├── retry_calendar_sync.py
│   ├── check_calendar.py       # calendar setup check: create, read, delete a test event
│   ├── set_frontend_db_password.py  # password for the read-only frontend role -> frontend/.env.local
│   └── latency_report.py
├── src/honda_voice/
│   ├── __init__.py
│   ├── config.py                 # pydantic-settings; builds LLM from env
│   ├── db/
│   │   ├── pool.py               # asyncpg pool (session pooler)
│   │   ├── catalog.py            # find/list/compare queries
│   │   ├── catalog_seed.py       # catalog JSON → validated rows → transactional upsert
│   │   ├── bookings.py           # reserve/cancel/lead/slot queries
│   │   └── calendar_sync.py      # background calendar mirror, retries, status
│   ├── domain/
│   │   ├── pricing.py            # format_inr_spoken(amount, lang)
│   │   ├── language.py           # session-language rule, per-language words (§4.1)
│   │   ├── datetime_resolve.py   # resolve + speak_slot(lang)
│   │   ├── phone.py              # normalize, 5+5 read-back, mask
│   │   ├── booking_guard.py      # read-back-before-write rules (rule 6)
│   │   ├── calendar_event.py     # booking -> calendar event (stable id, masked phone)
│   │   ├── competitors.py        # competitor model -> closest Honda (rule 11)
│   │   ├── advisor.py            # next-move rules from the caller profile (§20.3)
│   │   ├── recommend.py          # rank models for a need (§20.4)
│   │   ├── finance.py            # demo EMI from scheme + catalog price
│   │   └── lead_score.py         # hot / warm / cold
│   │   └── scope.py              # out-of-scope topics, price questions
│   ├── integrations/
│   │   └── gcalendar.py          # Calendar API client: create/get/delete event
│   ├── decisions/                # Phase 7 (§20)
│   │   ├── base.py               # SignalProvider protocol
│   │   ├── nano_signals.py       # gpt-5.4-nano structured output (default)
│   │   ├── laya_client.py        # optional local Laya provider (§23)
│   │   ├── questions.py          # profile questions (§20.2)
│   │   └── profile.py            # merge signals into the caller profile
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
│       ├── call_state.py         # per-call state shared across handoffs
│       ├── base_agent.py         # language, tts_node cleanup, capture_lead (both agents)
│       ├── sales_agent.py
│       ├── booking_agent.py
│       ├── text_transforms.py    # streaming tts_node transforms
│       ├── metrics.py
│       └── worker.py             # entrypoint, AgentSession config
├── certs/                        # public CA certificate for verified DB TLS (not a secret)
├── frontend/                     # from LiveKit agent-starter-react (MIT)
│   ├── app/api/{login,logout,token,bookings,catalog}/route.ts
│   ├── lib/server/               # config, passcode, session cookie, rate limits, read-only DB
│   ├── components/app/           # sign-in, privacy notice, side panel (bookings, models)
│   └── tests/                    # Vitest: auth, token claims, rate limits
├── evals/
│   ├── scenarios/*.yaml
│   ├── recordings/               # git-ignored
│   ├── run_text_evals.py
│   └── calendar_e2e.py           # Phase 4 exit check against the real calendar
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
| Wrong caller profile (nano misreads a turn) | A less relevant question or suggestion | Low stakes by design: no gate depends on it; one move per turn, which the LLM may skip; labelled set per language (§20.8) |
| Advisor feels pushy | Callers disengage | ≤ 1 question per turn, ≤ 4 per call, caller's question first, no push for browsers, callback on frustration (§20.3) |
| Profiling and callbacks (DPDP) | Consent and purpose | Explicit callback consent stored with time and text version; categories only; privacy notice updated (§20.5) |
| LangGraph adds per-turn latency | Slower agent | Phase 8 A/B; non-LLM router; no buffering nodes |
| In-memory rate limits (sign-in, tokens) | With several Next.js instances each keeps its own counters, so limits multiply; `X-Forwarded-For` is trusted for the client IP | Fine for one instance behind a trusted proxy; use a shared store (e.g. Redis) and the host's real-IP header before scaling |
| Shared demo passcode | Anyone who has it can start calls (cost) and see the masked bookings panel | Rotate after demos; real login (or phone-number calling) for production; tokens and calls are capped (10 a minute, 10-minute calls) |
| Supabase session pooler allows 15 clients | Connection errors with several workers or the FastAPI service | Worker pool `max_size=5`; move to the transaction pooler (port 6543, `statement_cache_size=0`) or a paid tier before scaling out |
| A second backend service (FastAPI) | More to deploy and keep in sync | Optional; shared `db/` + `domain/` modules; only behind §22.1 triggers |

---

## 20. Sales advisor and leads (Phase 7)

**Decision (2026-10-19, product owner):** the decision layer becomes a **sales-advisor layer**. It works out what the caller is looking for, steers the conversation toward a test ride or a callback, and turns the call into a scored lead. Three choices were made with it:
- **Caller understanding runs on Azure `gpt-5.4-nano` (structured output), not Laya.** Laya measured 67–78 % on Indic transcripts against nano's 96–100 % (§23.2b). Laya stays installed as an optional provider and a later distillation target (Phase 7.5).
- **The booking-confirmation gate is dropped.** The code guard in `domain/booking_guard.py` (rule 6) remains the only gate, and no model call is serial in the voice path.
- **One advisor-style SalesAgent, not a separate lead agent.** Callers move between needs and named models within seconds ("scooty for my mom" → "Activa price?" → "is the Dio lighter?"), and each handoff costs an extra LLM step (~1 s). Booking stays separate because it's a guarded step-by-step flow.

**Roles:** nano judges what the caller wants, code decides what to do about it, and the reply LLM writes the words. Nothing here decides prices, EMIs, dates, slots or phone validity.

### 20.1 Flow

```
caller turn ─► STT ─► reply LLM (SalesAgent) ──────────────────────────► TTS        unchanged; no added delay
                 │          ▲  coach line (one suggested move) + recommend_bikes / emi_examples tools
                 │          │
                 ▼          │
        nano signals (parallel, ~1.3 s) ─► caller profile ─► next-move rules (code, domain/advisor.py)
                                                 │
                         call end ───────────────┴─► calls row, lead + score, summary ─► Leads panel / CSV
```

- **Signals** fire when a caller turn is final, in parallel with the reply, over the last 4 turns plus agent state. Results merge into the profile and apply from the **next** turn. A late or failed call changes nothing; it never delays audio.
- **Same turn:** the reply LLM reads the caller's words itself. For a need with no model named, it calls `recommend_bikes` with what it heard, so the first answer doesn't wait for the profile.

### 20.2 Profile questions (`decisions/questions.py`)

| Key | Type | Values |
|---|---|---|
| `intent` | choice | `specific_model, price, compare, recommendation, finance, booking, cancel, competitor, out_of_scope, smalltalk, wants_human` |
| `use_case` | choice | `commute, family_errands, long_rides, college, delivery, weekend_fun, unknown` |
| `rider` | choice | `self, parent, child, spouse, other, unknown` |
| `priorities` | multi-choice | `price, mileage, power, ease, comfort, style, electric, safety` |
| `budget_inr` | amount | the most the caller wants to spend, in rupees, or null. **Code** maps it to `budget_band` (`under_80k, 80k_1_2l, 1_2l_2l, over_2l`): asked for a band directly, nano put "ninety thousand" under 80,000 (2/18 right) |
| `timeline` | choice | `this_week, this_month, 1_3_months, later_or_browsing, unknown` |
| `experience` | choice | `first_two_wheeler, upgrading, replacing, unknown` |
| `payment_interest` | multi-choice | `emi, exchange` |
| `objection` | choice | `price_high, waiting_time, comparing_brand, mileage_doubt, none` |
| `frustration`, `purchase_intent` | score 1–5 | calm … angry; browsing … buying this month |
| `wants_human` | probability | asks for a person or a callback |

The profile keeps a value only above a confidence threshold, and a later clear answer replaces an earlier one ("actually up to 1.5 lakh"). Categories only: no transcript text and no sensitive inferences (income, caste, religion, health).

### 20.3 Next-move rules (`domain/advisor.py`, pure)

Code picks **at most one** move per turn and adds it to the reply prompt as a single line, for the LLM to phrase or skip. Examples:

| Profile | Move |
|---|---|
| A model was asked about, use unknown | After answering, ask one question about use ("mostly office travel or longer rides?") |
| A need is known but no model chosen | Suggest 2 models from `recommend_bikes` |
| Parent, or first two-wheeler | Mention easy handling, then the test ride |
| `price_high` objection | Lower variant price and the demo EMI example (tool output only), "the showroom confirms actual offers" |
| `comparing_brand` | Honda strength only (competitor policy, rule 11) |
| `purchase_intent ≥ 4` or `this_week`/`this_month` | Offer a test ride; if declined, a callback |
| EMI interest | One demo example from `emi_examples` |
| Exchange interest | Showroom callback for an exchange quote (no exchange values) |
| `frustration ≥ 4` or `wants_human` | No more questions: offer a callback straight away |
| `later_or_browsing` | No pushing: answer, then offer to note the number for updates |

**Limits:**
- The caller's own question is always answered first.
- At most one discovery question per turn, and at most 4 per call.
- Never ask again about something already known.
- No moves during BookingAgent steps.

### 20.4 Tools

- `recommend_bikes(use_case, rider, priorities, budget_inr, experience)`: code ranks on-sale models using `bike_fit` (ease, weight, seat height, uses) and the catalog (segment, mileage, price), and returns the top 2–3 with one reason each. The LLM never picks or invents a bike.
- `emi_examples(model, variant?)`: `domain/finance.py` computes EMI = P·r·(1+r)^n / ((1+r)^n − 1) from the catalog ex-showroom price and each demo scheme. Spoken from tool output only, like prices (rule 4), always with "example only; the showroom confirms the actual terms".
- `capture_lead` (extended): asks for explicit callback consent, uses the same phone validation and 5+5 read-back as bookings, and stores consent with its time and text version.

### 20.5 Leads and the customer list

- **Every call** gets a `calls` row and a `caller_profiles` row, including anonymous ones. These feed insights such as top needs and objections by language.
- **Lead score** (`domain/lead_score.py`, code): from `purchase_intent`, timeline, engagement (models discussed, recommendation accepted), booking and consent. Hot ≥ 70, warm 40–69, cold < 40.
- **Call end:** nano writes a 2–3 sentence summary with no phone number or name, for the person calling back. It runs after the call, so the caller never waits for it.
- **Consenting callers** get a `customers` row. A lead, or a booking, links to it, and that table is the customer list. A later Honda CRM/DMS integration would read `customers` and `leads`.
- **Frontend:** a **Leads** panel sorted by score (phone masked, summary, status) with a CSV export, plus a live **Caller profile** panel next to the transcript. The panels are behind the sign-in cookie and read masked views.

### 20.6 Schema — `005_advisor_leads.sql`

RLS is enabled on every new table, and the frontend reads only masked views in schema `frontend`.

| Table | Columns |
|---|---|
| `finance_schemes` (demo) | `id, name, interest_rate_pct, tenure_months int[], min_down_payment_pct, processing_fee_inr, is_demo bool not null default true, as_of` |
| `bike_fit` | `bike_id pk/fk, kerb_weight_kg, seat_height_mm, ease_score 1–5, use_cases text[], rider_fit text[], source, is_estimate bool` |
| `customers` | `id uuid, phone unique, name, preferred_language, callback_consent bool, consent_at, consent_text_version, first_seen_at, last_contact_at` |
| `calls` | `id, room_name unique, started_at, ended_at, languages text[], customer_id null, outcome (info/booked/lead/cancelled/dropped), summary, max_purchase_intent` |
| `caller_profiles` | `call_id pk/fk, use_case, rider, priorities text[], budget_band, timeline, experience, payment_interest text[], objections text[], models_discussed text[], models_suggested text[], confidence jsonb, updated_at` |
| `call_signals` | `id, call_id, turn_index, question, answer, confidence, provider (nano/laya), latency_ms, created_at` |
| `leads` (extended) | + `customer_id, call_id, score, score_value, status (new/contacted/converted/closed), next_action, callback_after, contacted_at, interest_bike_id` |
| `bookings` (extended) | + `customer_id, call_id` |

### 20.7 Catalog completion (approved 2026-10-19)

- **Real values with a source wherever they exist:** variant prices, mileage, colours, features and the on-road breakdown, from honda2wheelersindia.com first, then BikeWale, Autocar India or ZigWheels. The URL and date go in the existing source fields.
- **Estimates only for descriptive values** (fit tags, a missing mileage or size), flagged in `unverified_fields` / `bike_fit.is_estimate`: used for ranking, **never spoken** (unverified fields never reach the LLM; stricter than "say around", decided while building step 1).
- **Prices are never estimated.** A variant price that can't be sourced stays empty, and the agent gives the model's sourced range instead.
- **Empty values that are correct stay empty:** cc on electric scooters, range on petrol bikes, booking calendar errors that never happened, notes.

### 20.8 Evaluation

- **Labelled set:** about 150 turns across all six languages, including multi-turn context. ≥ 90 % accuracy per language for `use_case`, `rider`, `priorities`, `timeline`; ±1 agreement on the scores for ≥ 80 % of turns. Compared per provider (nano, Laya).
- **New scripted conversations:**
  - mom's scooter (ease plus mileage);
  - budget commuter;
  - delivery rider asking about electric;
  - price objection, with EMI from the tool only;
  - just browsing (no push);
  - frustrated caller (callback offered, no questions);
  - callback consent producing a lead with the right score.
- **Guarantees:** all existing scenarios still pass, 0 invented prices or EMIs, and p50 TTFA unchanged (±50 ms), since the signals run in parallel.


### 20.9 Step 2 as built (2026-10-20)

**Code:**

| File | Role |
|---|---|
| `decisions/questions.py` | Questions, schema and budget bands |
| `decisions/nano_signals.py` | Strict JSON schema, `reasoning_effort=minimal`, no retries |
| `decisions/laya_client.py` | Optional Laya provider |
| `decisions/profile.py` | Merge rules |
| `agent/advisor.py` | Runtime: one background request per committed caller message (`conversation_item_added`, so discarded drafts don't count); 8 s timeout; a failure is logged and skipped |
| `db/calls.py` | Call row, per-turn signals, profile, outcome; bookings linked by room, leads by `call_id` |

`ADVISOR=shadow` records signals without changing the call (the default until step 3 turned it `on`).

**Labelled set** (`evals/signals/cases.yaml`, `uv run python -m evals.signals_labelled`):
- 25 situations × 6 languages = 150 turns, 5 of them multi-turn.
- **nano: 96–99 % per language** (target 90 %), p50 1.2 s, p90 1.4 s per turn, run in parallel with the reply.
- **Laya `multilingual`: 49–57 %.**

The first nano run scored 90–94 %. Three changes to the questions fixed it:
- budget asked as an amount, with code picking the band;
- "compare" limited to two Honda models;
- "self" made the default rider when callers describe their own riding.

These were tuned on the same set, so live calls are the real check.


### 20.10 Step 3 as built (2026-10-20)

**Ranking (`domain/recommend.py`):** on-sale models only, scored on:
- use case (+3);
- ease, for a parent, spouse or child rider, a first two-wheeler or an "ease" priority: (ease − 3) × 1.5, +1.5 if gearless, −4 if tagged for experienced riders;
- mileage, capped at +2 so it never decides alone;
- electric (+5 or −3), power, price, comfort, style, ABS.

Budget: up to 5 % over still counts; when fewer than 2 models fit, the cheapest over-budget ones are added and marked. Weak fillers (< 60 % of the best score) are dropped. Reasons come from verified facts only. Tuning examples:
- An early version recommended the Shine 100, a geared motorcycle, for a mother wanting easy handling; the gearless bonus and the mileage cap fixed it.
- "Good mileage" briefly ranked electric scooters first; that bonus was lowered.

**Tools:**
- `recommend_bikes`: tool arguments win; the caller profile fills in anything this turn didn't say. Suggested models go into `models_suggested`.
- `emi_examples`: amounts from `domain/finance.py`, returned in `*_spoken` fields, so the price checks cover them.

**Moves (`domain/advisor.py`), in priority order:** callback, price objection, EMI, exchange, browsing (no push), suggest, test ride, then discovery questions (use, rider, budget, timeline; at most 4).
- Each move is suggested once per call.
- No moves during booking; after a callback offer, no more selling.
- The coach line is computed once per caller turn, so tool steps reuse it.
- `ADVISOR=on` adds it; `shadow` records only.

**Prompt changes:**
- EMI is in scope via the tool; exchange is a callback.
- `recommend_bikes` is used for needs.
- A caller who says they want to buy soon, without asking for a test ride, is offered one of that model. The rule sits in the test-ride section after "call start_booking right away" and has no example question: worded as "ask whether they'd like a test ride ('Would you like…?')", it made the agent answer "Can I test ride the Hornet tomorrow?" with "shall I book it?" in about half the runs.
- The caller is addressed with neutral forms. Hinglish replies had used feminine forms for the caller ("bata dengi").
- The intro now leads with "find the right Honda for your needs".

**Scenarios (`advisor.yaml`, real nano signals between turns):** mom's scooter, budget commuter, Tamil delivery EV, price objection with EMI, browsing, frustrated caller, ready buyer.
- 6 of 7 passed on the first run.
- The ready-buyer scenario now checks `said_any` for the whole call, since the offer can come in turn 2 or 3 and a yes may already start the booking.
- After the prompt fixes: Hornet booking 4/4, ready buyer 3/3.


### 20.11 Step 4 as built (2026-10-20)

**Callback consent (DPDP)**
- `capture_lead` with a number first returns `prompts.LEAD_CONSENT`: the number read back 5+5 and "may the showroom call you about this? We'll use the number only for this callback". It's spoken from code, without a second LLM call; `_fixed_tool_reply` now lives in `MultilingualAgent` with a per-agent `FIXED_SAY_TOOLS`.
- It saves only on a later call with `caller_confirmed=true`, in a later caller turn, for the same number (`CallState.lead_pending`). A "no" saves nothing.
- Saving upserts `customers` (one row per number) with `callback_consent`, `consent_at` and `consent_text_version` (`db.bookings.CONSENT_TEXT_VERSION`; bump it when the wording changes). The lead then links to that customer. Consent is only ever added, never removed by a later call.
- Without a number (two failed tries), a lead is saved with no customer and no consent, as before.

**At call end** (`Advisor.close`, in the worker's shutdown, so the caller never waits):
- nano writes a 2–3 sentence English summary. The prompt asks for no names, numbers or codes, and to say "the caller" (never he/she). `decisions.summary.scrub` also removes digit runs of 5+, "HND …" codes and known names.
- `domain/lead_score.py` scores the call's leads:
  - buying interest: (max − 1) × 12;
  - timeline: this week 25, this month 20, 1–3 months 15;
  - models discussed or suggested: 5 each, up to 10;
  - booked: +20; asked about payment: +5; frustrated: −10.
  - Bands: hot ≥ 70, warm 40–69, cold < 40, plus one next-action line.
- `calls.finish` links this call's bookings to customers (a booking is **not** a callback consent), scores its leads, and sets `calls.customer_id`.

**Frontend** (sign-in cookie, masked views only):
- `GET /api/leads`: hottest first.
- `GET /api/leads/export`: CSV; cells starting with `= + - @` are prefixed with `'` against CSV injection.
- `GET /api/profile?room=`: only token-route room names, else 400.
- Side panel: a **Leads** tab with a CSV download, and a live **Caller profile** card (polled every 3 s) during a call.
- Privacy notice: the categories noted, the call summary, and consent before any callback.

**Checks:**
- Unit tests for consent (same-turn yes and a different number are refused), score bands and actions, summary scrubbing, and the fixed consent line.
- Database tests: consent kept on upsert; `finish` links bookings, scores leads and sets the customer.
- Vitest: 18 tests, including the new routes and CSV injection.
- Scenarios: callback with consent → 1 lead + 1 consented customer; declined → none. The no-licence scenario gained its "yes" turn.

**Harness:** the price checks accept any tool output from earlier in the same call. A Tamil caller's "ok" got the previous turn's price repeated and was flagged as not from a tool. Consequence: a turn's `price: true` can also be met by repeating an earlier price.

**Related fix:** for a test-ride request, the prefetched facts no longer add the "which model?" note for family names. With it, "test ride the CB350 tomorrow" got a "which CB350?" question instead of the handoff (1 run in 3); BookingAgent asks which model itself.

---

## 21. LangGraph orchestration (Phase 8, future)

### 21.1 When LangGraph is worth adding

Native LiveKit handoffs cover the POC. LangGraph earns its cost if the product grows into: multi-step flows with branches (finance or EMI pre-qualification, exchange-vehicle valuation), human-in-the-loop interrupts (a sales manager approves a discount), durable state across calls (callbacks that resume a conversation), or parallel tool branches.

### 21.2 Design constraints

- Graph: `route → sales_node | booking_node → END`. Each node is a **streaming** LLM node with the same tools as §7.
- **No guardrail node.** Speech transforms stay in `tts_node` (§9). A node that post-processes the full reply forces buffering and erases streaming.
- Router: `LG_ROUTER=decision` (the `intent` signal mapped to a node; serially this needs a fast provider, and Laya's intent accuracy was weak in §23.2b, so measure first), `rules` (sticky state: stay in the current node unless a handoff tool fired), or `llm`. Prefer `decision` or `rules`.
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

## 23. Laya, self-hosted (optional signal provider for §20)

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

### 23.2b Caller-understanding comparison (2026-10-19) — Laya replaced by nano

48 sentences (8 caller needs × 6 languages, written as Sarvam codemix transcribes them: native script plus English words), 6 questions each (intent, use, rider, priority, timeline, finance). Share of labelled answers right:

| Provider | en | hi | ta | te | ml | kn | Latency |
|---|---|---|---|---|---|---|---|
| Laya `multilingual` (local GPU) | 85 % | 67 % | 67 % | 74 % | 67 % | 78 % | 28 ms |
| Laya `english` | 78 % | 52 % | 41 % | 48 % | 52 % | 44 % | – |
| Azure gpt-5.4-nano, JSON schema | 100 % | 100 % | 96 % | 100 % | 100 % | 100 % | p50 1.3 s |
| Azure gpt-5.4-mini, JSON schema | 96 % | 96 % | 100 % | 96 % | 96 % | 96 % | p50 1.9 s |

- **Laya's misses include English basics:** "my mother" was answered as `child`.
- **Translating first would not help.** Laya reaches only 85 % on clean English, and a translation needs an LLM call that can classify directly.
- **nano's latency is acceptable** because signals run in parallel and apply from the next turn.
- **Most of the remaining nano/mini misses were a labelling flaw:** "easy to handle and good mileage" names two priorities, so `priorities` is now a multi-choice field.
- **Decision (product owner, 2026-10-19):** nano is the signal provider. Laya stays optional, and could later be trained on nano's labels (`laya train`).
- **Probe scripts:** kept out of the repo; they become `evals/signals_labelled.py` in Phase 7 step 2.

### 23.3 If Laya is used again

`decisions/laya_client.py` implements the same `SignalProvider` protocol as nano: one `POST /v1/systemone` per turn carrying the §20.2 questions, with a timeout. Results go to `call_signals` with `provider = laya-<checkpoint>@0.4.7`, so the two providers can be compared from the same table. Switch `SIGNAL_PROVIDER=laya` only if it matches nano on the §20.8 labelled set.

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