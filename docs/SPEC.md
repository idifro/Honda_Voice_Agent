# SPEC — Honda 2-Wheeler Multilingual Voice Sales Agent (POC)

Lives at `docs/SPEC.md`. CLAUDE.md holds the hard rules; this document holds the design.

---

## 1. Goal and success criteria

A caller speaks Hindi/Hinglish, English, Tamil, Telugu, Malayalam or Kannada to a browser-based voice agent for one Honda showroom, and the agent replies in the caller's language. The agent answers questions about models, variants, prices, and mileage, and books a test ride that lands in Supabase and is mirrored to the showroom's Google Calendar.

The POC **passes** if it meets these on the eval set (§15) and in five live demo runs:

| Metric | Target |
|---|---|
| Time to first agent audio after caller stops speaking (p50 / p90) | ≤ 1.2 s / ≤ 2.0 s |
| Model name correctly resolved from speech (30 recorded utterances: 10 Hindi/English, 5 each Tamil, Telugu, Malayalam, Kannada) | ≥ 90 % overall, ≥ 80 % per language |
| Reply language matches the caller's language (§4.1) | ≥ 95 % of turns |
| Scripted booking conversations completed end-to-end | ≥ 80 % |
| Bookings with wrong phone, date, or model persisted | 0 |
| Spoken prices that differ from catalog | 0 |
| Agent turns over 2 sentences / ~40 words | ≤ 10 % |

## 2. Scope

**In scope:** one showroom; the full current Honda India lineup (33 models) with variants; six languages (Hindi/Hinglish, English, Tamil, Telugu, Malayalam, Kannada) with the reply language following the caller (§4.1); price/spec/mileage Q&A; simple two-model comparison within Honda; test-ride booking and cancellation; callback lead capture as fallback; browser client with visualizer, transcript, and live bookings panel.

**Out of scope (non-goals):** PSTN/SIP telephony, Indian languages beyond the six above, on-road price calculation, finance/EMI quotes, scraping the OEM website, multiple showrooms, an admin UI, and authentication beyond a shared demo passcode.

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
     ├─ SalesAgent   — greeting, consent, catalog Q&A, comparisons, competitor pivot
     └─ BookingAgent — collects + confirms details, reserves slot, cancels
   Decision layer (Phase 7): Jev — per-turn signals run in parallel (never delays audio);
                             booking-confirmation gate is the only serial Jev call (§20)
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
| Turn detection | `livekit.agents.inference.TurnDetector` (livekit-agents 1.8.8; the old `turn_detector.multilingual` plugin is deprecated) | Prevents cut-offs at mid-sentence pauses. `v1` runs on LiveKit Cloud and gets its language list from the server (check ta/te/ml/kn in step 6). It falls back to a local `v1-mini` model, which supports only `hi` and `en` of our six. For unsupported languages, end of turn comes from VAD silence. Phase 10 (self-hosted LiveKit) leaves only the local model. |
| Interruptions | enabled; `min_interruption_duration` ≈ 0.5 s | Caller barge-in stops agent audio. |
| LLM | Azure OpenAI deployment via `LLM_PROVIDER=azure` | Default: **`gpt-5.4-mini`** (or the newest *mini* tier available in your Azure region) with `reasoning_effort` at its lowest setting (`none`, or `minimal` on `gpt-5`/`gpt-5-mini`). Do **not** use full `gpt-5`/`gpt-5.4`/`gpt-5.5` or any `-pro` model in the live voice path; reasoning adds seconds of time-to-first-token. Phase 1 benchmarks the chosen mini model against one alternative (e.g. `gpt-5-mini` at `minimal`) on TTFT, tool-call accuracy and Hinglish quality. `max_tokens` ≈ 150 (reasoning tokens count against it). GPT-5-series models only accept `temperature=1`; don't set it. Deploy in the Azure region/deployment type closest to India that offers the model (check the Foundry catalog for Central/South India; Global Standard may route outside India). |
| TTS | `bulbul:v4-flash`, `speaker=TTS_SPEAKER` (`aarti_hi_customer`), `language_code` = session language, set per turn | One voice for all six languages (tested 2026-10-15; language-specific voices were no better and `kavitha_te_conversation` garbled English words). Use `dict_id` (Sarvam pronunciation dictionary) for: Activa, Shine, SP125, Hornet, Unicorn, H'ness, CB350, Honda. |
| Noise cancellation | LiveKit BVC if available on plan | Optional. |

**Azure setup (pre-Phase 1, 2026-10-15):** resource in **South India**, both deployments Global Standard (the subscription has 0 regional-Standard quota there). The content filter must use **Streaming mode = Asynchronous Filter**; the default filter buffers output and added ~300 ms to TTFT. With the async filter, a direct streaming test from India (no tools) measured TTFT median / p90 of `gpt-5.4-mini` (`reasoning_effort=none`) at 531 / 707 ms and `gpt-4.1-mini` at 718 / 808 ms. An East US 2 resource measured 1.5–2.2 s, so a resource in India is required. `gpt-4.1-mini` replaces `gpt-5-mini` as the comparison model because it doesn't reason before answering.

**Sarvam v4 (pre-Phase 1, 2026-10-15):** STT and TTS switch to `saaras:v4` and `bulbul:v4-flash` (configured by `STT_MODEL`, `TTS_MODEL` and `TTS_SPEAKER`). In a TTS→STT round-trip test on synthetic audio, `saaras:v4` + `codemix` kept English words and model names in Latin script and got the reference code exactly (`HND7K3Q9`), while `transcribe` mode wrote everything in Devanagari. Without `keyterms`, it misheard "Unicorn" as "Anicon" and "H'ness" as "HNS". With model-name `keyterms`, all names came out right, but "HND" in the reference code turned into "H'ness", so `keyterms` must not be active while a caller reads out a reference code (cancellation). `bulbul:v4-flash` time to first audio byte was 52–85 ms (`bulbul:v3`: 56–285 ms). v4-flash uses different voice names (`aarti_hi_customer`, `shubh_enhi_ads`, `ishita_enhi_customer`, …; `shubh` alone isn't valid). Voice chosen: `aarti_hi_customer`, which was the only tested voice whose "Unicorn" came back correct. Every voice mispronounced "H'ness", so it needs a pronunciation-dictionary entry, and the SDK parameter is `language_code` (sarvamai 0.1.37). Real callers' speech still needs the Phase 1 recordings.

**Chosen values (fill in during Phase 1):** STT_MODE = `____`, LLM = `____`, measured p50 TTFA = `____`.

### 4.1 Language handling

Six languages: `hi-IN` (Hinglish), `en-IN`, `ta-IN`, `te-IN`, `ml-IN`, `kn-IN` (`SUPPORTED_LANGUAGES`). **Code decides the reply language; the LLM never guesses it** (same principle as rule 12). Measured 2026-10-15: when the prompt only said "reply in the caller's language", Tamil, Telugu and Malayalam callers got Hinglish replies. With the language passed explicitly, the replies matched (Telugu came back in Roman script once; see risks).

- **Detection:** STT runs with `language="unknown"`. Each final transcript carries a detected language (probability 1.0 on every test clip).
- **Session language (`domain/language.py`, pure, unit-tested):** starts as `GREETING_LANGUAGE` (`ta-IN`). It switches to the detected language when a final transcript has ≥ 3 words or a detection probability ≥ 0.9, so one-word answers like "ok", "haan" or "yes" don't flip it. An explicit request ("English mein bolo", "Tamil la pesunga") always switches.
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

### `001_init.sql`

```sql
create extension if not exists pg_trgm;

create table public.showrooms (
  id            text primary key,                 -- 'demo-showroom'
  name          text not null,
  city          text not null,                    -- reference city for ex-showroom prices
  open_time     time not null default '10:00',
  close_time    time not null default '19:00',
  closed_dow    int[] not null default '{}',      -- ISO weekday numbers, e.g. '{7}' for Sunday
  slot_minutes  int  not null default 30,
  tz            text not null default 'Asia/Kolkata'
);

create table public.bikes (
  id              text primary key,               -- 'activa-110'
  name            text not null,                  -- 'Honda Activa 110'
  category        text not null check (category in ('scooter','commuter','premium-commuter','mid','premium')),
  engine_cc       numeric(5,1) not null,
  mileage_kmpl    numeric(4,1),                   -- claimed/typical, note source in specs
  specs           jsonb not null default '{}',    -- brakes, tyres, fuel tank, kerb weight, features
  pitch           text not null,                  -- 1–2 sentence USP the agent may paraphrase
  price_as_of     date not null,
  price_source    text not null                   -- URL or 'Honda price list, <city>'
);

create table public.bike_variants (
  id               text primary key,              -- 'activa-110-std'
  bike_id          text not null references public.bikes(id) on delete restrict,
  variant_name     text not null,                 -- 'STD', 'DLX', 'H-Smart'
  ex_showroom_inr  integer not null check (ex_showroom_inr > 0)
  -- no stored spoken_price: tools render it per call in the session language (§9)
);

create table public.bike_aliases (
  alias   text primary key,                       -- lower-cased, any script: 'activa', 'aktiva', 'ऐक्टिवा', 'ஆக்டிவா', 'యాక్టివా'
  bike_id text not null references public.bikes(id) on delete cascade
);
create index bike_aliases_trgm on public.bike_aliases using gin (alias gin_trgm_ops);

create table public.slots (
  id           bigserial primary key,
  showroom_id  text not null references public.showrooms(id),
  bike_id      text not null references public.bikes(id),
  starts_at    timestamptz not null,
  capacity     int not null default 1 check (capacity >= 0),   -- demo units available
  booked       int not null default 0 check (booked >= 0),
  unique (showroom_id, bike_id, starts_at),
  check (booked <= capacity)
);
create index slots_lookup on public.slots (showroom_id, bike_id, starts_at);

create table public.bookings (
  id                    bigserial primary key,
  reference             text unique not null,          -- 'HND-7K3Q9'
  slot_id               bigint not null references public.slots(id),
  customer_name         text not null,
  customer_phone        text not null check (customer_phone ~ '^[6-9][0-9]{9}$'),
  status                text not null default 'CONFIRMED' check (status in ('CONFIRMED','CANCELLED')),
  calendar_event_id     text,
  calendar_sync_status  text not null default 'PENDING' check (calendar_sync_status in ('PENDING','SYNCED','FAILED')),
  calendar_sync_error   text,
  consent_given         boolean not null,
  language              text not null default 'hi-IN', -- session language at booking (§4.1)
  room_name             text,                          -- LiveKit room, for tracing
  created_at            timestamptz not null default now()
);
create unique index one_active_booking_per_phone_slot
  on public.bookings (slot_id, customer_phone) where status = 'CONFIRMED';

create table public.leads (                            -- fallback when booking can't complete
  id             bigserial primary key,
  customer_name  text,
  customer_phone text check (customer_phone is null or customer_phone ~ '^[6-9][0-9]{9}$'),
  interest       text,                                 -- bike_id or free text
  reason         text not null,                        -- 'no_slot', 'caller_requested', 'error'
  consent_given  boolean not null,
  language       text not null default 'hi-IN',        -- callback in the caller's language
  created_at     timestamptz not null default now()
);

alter table public.showrooms     enable row level security;
alter table public.bikes         enable row level security;
alter table public.bike_variants enable row level security;
alter table public.bike_aliases  enable row level security;
alter table public.slots         enable row level security;
alter table public.bookings      enable row level security;
alter table public.leads         enable row level security;
```

### `002_booking_functions.sql`

```sql
create or replace function public.reserve_slot(
  p_slot_id bigint, p_name text, p_phone text, p_reference text,
  p_consent boolean, p_room text, p_language text
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

  insert into public.bookings (reference, slot_id, customer_name, customer_phone, consent_given, room_name, language)
  values (p_reference, p_slot_id, p_name, p_phone, p_consent, p_room, p_language)
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

**Aliases in every script:** each model gets aliases in Latin script plus Devanagari, Tamil, Telugu, Malayalam and Kannada spellings (e.g. `ஆக்டிவா`, `యాక్టివా`, `ആക്ടിവ`, `ಆಕ್ಟಿವಾ`), because `codemix` usually but not always keeps model names in Latin script. Phase 1 recordings add the misspellings STT actually produces.

## 7. Tools

All tools are `@function_tool` methods with pydantic-typed arguments. Each returns a small dict meant for the LLM to speak from, never raw rows. Spoken strings (`spoken_price`, slot times) are rendered in the **session language** (§4.1).

### SalesAgent

| Tool | Args | Behavior |
|---|---|---|
| `find_bike` | `query: str` | Exact alias match → trigram similarity (≥ 0.4) → none. Returns `{bike_id, name, category, engine_cc, mileage_kmpl, pitch, variants:[{variant_name, spoken_price}], price_note}`. `price_note` = "ex-showroom {city}, {price_as_of}". If ambiguous, returns up to 3 candidates so the agent can ask which one. |
| `list_bikes` | `category?: str, max_price_inr?: int` | Up to 4 matches, cheapest first, with `spoken_price` of the base variant. |
| `compare_bikes` | `a: str, b: str` | Both must be Honda. Returns side-by-side key fields only. |
| `start_booking` | `bike_id?: str` | Hands off to `BookingAgent` with chat context. |
| `capture_lead` | `name?, phone?, interest?, reason` | Fallback path; writes to `leads`. |

### BookingAgent

| Tool | Args | Behavior |
|---|---|---|
| `check_slots` | `bike_id, day_ref, time_24h?` | `day_ref` ∈ `today, tomorrow, day_after, mon…sun, YYYY-MM-DD`. Resolves via `datetime_resolve` (§10). Returns up to 3 nearest open slots as spoken strings in the session language ("kal shaam 5 baje", "நாளை மாலை 5 மணி"). |
| `validate_phone` | `digits: str` | Normalizes (strips +91/0/spaces), validates `^[6-9]\d{9}$`, returns `readback` grouped 5+5. |
| `book_test_ride` | `slot_id, name, phone, caller_confirmed: bool` | Rejects if `caller_confirmed` is false. When `JEV_MODE=enforce`, also requires the Jev confirmation gate to pass (§20.3). Calls `reserve_slot` (with the session language). On success, schedules the calendar mirror task and returns `{reference_spoken}` (spelled with gaps: "H N D, 7 K 3 Q 9"). On `SLOT_UNAVAILABLE`, returns the next 2 open slots. |
| `cancel_test_ride` | `reference, phone` | Calls `cancel_booking`. |
| `back_to_sales` | — | Hands back to `SalesAgent`. |

## 8. Conversation design (`agent/prompts.py`)

**Persona:** a friendly, concise showroom executive. The voice is female (`aarti_hi_customer`), so first-person Hindi verbs use feminine forms ("kar sakti hoon", "confirm kar rahi hoon"); if `TTS_SPEAKER` changes to a male voice, switch the forms in `prompts.py` too. Reply in the session language given each turn (§4.1), never by guessing: Hindi as Roman-script Hinglish; Tamil, Telugu, Malayalam and Kannada in native script; brand/model names and words like *test ride* and *ex-showroom* in English. Feminine first-person forms apply in every language that marks gender on verbs. `prompts.py` holds the per-language fixed lines (greeting, consent, re-confirm, competitor pivot, out-of-scope deflection).

**Greeting (Tanglish, with consent and language choice):** "வணக்கம்! Honda showroom-க்கு welcome. இந்த call quality-க்காக record ஆகலாம், continue பண்ணலாமா? Tamil, English, Hindi, Telugu, Kannada, Malayalam, எந்த language-ல பேசலாம்?" It is stored in Tamil script with English words, because TTS reads that natively; Roman-script Tanglish also round-tripped correctly. The caller's first reply sets the session language (§4.1) and answers consent. If they answer only the language question, the agent asks for consent once more in that language. A booking cannot be made without `consent_given = true`. The greeting is ~12 s long; trim it after the Phase 1 listening test.

**Brevity:** at most 2 short sentences per turn, and end with a question that moves the call forward. This is enforced through the prompt and `max_tokens`. Over-length turns are **logged, not truncated**.

**Prices:** speak only the `spoken_price` from tool output, always with "ex-showroom". If asked for on-road price, say it varies by city and RTO charges and offer a callback from the showroom.

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
- Digits stay as numerals in Hindi too. **Phase 1 checks** pronunciation in each language; any fix goes inside this function only.

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

# Decision layer (Phase 7) — provider: jev | laya | llm_fallback
DECISION_PROVIDER=jev
# mode: off | shadow | enforce (JEV_MODE kept as the name for compatibility)
JEV_MODE=off
JEV_API_KEY=<key>                # TypeSafe AI direct, or route via OpenRouter
JEV_MODEL=<pinned-version>       # pin an exact version, e.g. the one verified in Phase 7
JEV_TIMEOUT_MS=600
LAYA_API_KEY=<key>               # Vercel AI Gateway or Convai Innovations direct (§23)
LAYA_BASE_URL=<gateway or vendor endpoint>
LAYA_MODEL=convaiinnovations/laya   # pin; '-free' ID stops serving after 2026-10-31

# Optional API service (§22) — when enabled, the frontend calls it instead of the DB
API_BASE_URL=                    # e.g. http://localhost:8000; empty = Next.js routes read the DB
API_SERVICE_TOKEN=<shared secret between Next.js server and FastAPI>

# LangGraph (Phase 8) — native | langgraph
ORCHESTRATOR=native
LG_ROUTER=jev                    # jev | rules | llm

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
*Exit:* STT mode, LLM, and the price-speech format are recorded in §4/§9; p50 TTFA ≤ 1.2 s in console; per-turn metrics are logged; a console call that switches language mid-call gets replies in the new language.

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

**Phase 7 — Jev decision layer (§20).** `DecisionModel` interface + Jev client; migration 003; parallel per-turn signals; confirmation gate. Run in `shadow` mode across the full eval suite and demo calls, then switch to `enforce` only for gates that clear their targets.
*Exit:* §20.5 targets met on the labelled set; p50 TTFA unchanged (±50 ms) vs Phase 6 on non-confirmation turns.

**Phase 8 — LangGraph orchestration, A/B (§21).** Build the graph behind `ORCHESTRATOR=langgraph` without removing the native path. Run the latency A/B across three router configs.
*Exit:* latency report comparing configs in §21.3; decision recorded here (adopt / adopt with Jev router / stay native) with reasons.

**Phase 9 — Optional FastAPI service (§22).** Only if a trigger in §22.1 applies.
*Exit:* frontend works with `API_BASE_URL` set and with it empty; Next.js no longer holds `DATABASE_URL` when the API is enabled; API tests pass.

**Phase 10 — Self-hosted LiveKit (§24).** Replace LiveKit Cloud with the open-source server in Docker. Only `LIVEKIT_URL` / keys change; no code changes.
*Exit:* full browser call and booking work against `ws://localhost:7880`; latency report re-run and compared with Cloud.

**Phase 11 — Local Postgres (§24).** Replace hosted Supabase with `postgres:16` in Docker, migrations applied on first start, catalog re-seeded. Only `DATABASE_URL` changes.
*Exit:* all integration tests and evals pass against local Postgres; data export/import path from Supabase documented.

Laya (§23) is not a separate phase: it is added in Phase 7 as a second `DecisionModel` provider and compared in the same labelled eval.

## 15. Testing and evals

**Unit tests (`tests/`, no network):**
- `test_pricing.py`: rounding, lakh/hazaar boundaries, the "approximately" prefix, each of the six languages.
- `test_language.py`: session-language rule (short answers don't switch, ≥ 3-word or high-probability turns do, explicit requests do).
- `test_datetime_resolve.py`: today/tomorrow/weekday across week boundaries, past times, closed days, snapping, IST at midnight, `speak_slot` in each language.
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

- Spoken consent at the greeting is stored on each booking or lead.
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
├── data/
│   ├── catalog/                # one JSON per model + _index.json
│   └── migrations/
│       ├── 001_init.sql
│       └── 002_booking_functions.sql
├── scripts/
│   ├── build_catalog.py        # regenerates data/catalog/
│   ├── seed_catalog.py
│   ├── generate_slots.py
│   ├── retry_calendar_sync.py
│   └── latency_report.py
├── src/honda_voice/
│   ├── __init__.py
│   ├── config.py                 # pydantic-settings; builds LLM from env
│   ├── db/
│   │   ├── pool.py               # asyncpg pool (session pooler)
│   │   ├── catalog.py            # find/list/compare queries
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
│   │   ├── jev_client.py         # Jev implementation, timeout + version pin
│   │   ├── laya_client.py        # Laya implementation (§23)
│   │   ├── llm_fallback.py       # same interface via LLM structured output (baseline)
│   │   └── questions.py          # typed question definitions (§20.2)
│   ├── graph/                    # Phase 8
│   │   ├── state.py
│   │   ├── router.py             # jev | rules | llm
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
| Jev accuracy on code-mixed Hinglish transcripts | Wrong routing or gate decisions | Shadow mode + labelled set before enforce (§20.5) |
| Jev is a new product (released Sept 2026) | API changes, availability | Version pin; `DecisionModel` interface with LLM fallback; gate fails safe to re-confirmation |
| LangGraph adds per-turn latency | Slower agent | Phase 8 A/B; non-LLM router; no buffering nodes |
| Laya pricing/latency after the free period | Cost or slower gate | Shadow-compare with Jev before Oct 31; keep provider switchable |
| A second backend service (FastAPI) | More to deploy and keep in sync | Optional; shared `db/` + `domain/` modules; only behind §22.1 triggers |

---

## 20. Jev decision layer (Phase 7)

Jev (TypeSafe AI) is a non-generative "System One" model: given a context and typed questions, it returns a **choice** from fixed options, a **score** on a rubric, or a **probability** that a statement is true, each with a confidence value. It cannot produce free text, so it complements the LLM rather than replacing it. **Use Jev for judgments, code for rules, and the LLM for replies.**

Verify the current API shape, limits, and model version at docs.typesafe.ai before building, and pin the version in `JEV_MODEL`.

The decision layer is **provider-neutral**: everything in this section applies equally to Laya (§23). `DECISION_PROVIDER` picks the implementation behind the `DecisionModel` interface; thresholds and gates don't change.

### 20.1 Where Jev sits

| Use | Path | Effect on latency |
|---|---|---|
| Per-turn signals (intent, competitor, out-of-scope, frustration, wants-human, purchase intent) | **Parallel**: fired when the user turn completes, alongside the LLM. Results apply from the *next* turn and to logging. | None on the current turn |
| Booking-confirmation gate | **Serial**, only on the turn where the caller answers the confirmation summary | One Jev call (~70–500 ms per vendor figures) on one turn per booking |
| LangGraph router (Phase 8) | Serial, every turn when `ORCHESTRATOR=langgraph` and `LG_ROUTER=jev` | One Jev call per turn |

Jev never decides prices, dates, slot availability, or phone validity. Those stay in `domain/` and the DB.

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

### 20.3 How signals are used (`JEV_MODE=enforce`)

- **Confirmation gate:** `book_test_ride` proceeds only if `confirms_booking ≥ 0.85` and `corrects_detail = none`. Between 0.5 and 0.85, or on timeout or error, the agent re-confirms once more ("Bas confirm kar rahi hoon — kal shaam 5 baje, Activa 125, theek hai?"). Below 0.5, or any correction, the agent goes back to the field in question. This catches answers like "haan… nahi ruko, kal nahi" where the LLM might still set `caller_confirmed=True`.
- **Escalation:** `wants_human` (confidence ≥ 0.8), or `frustration ≥ 4` on two consecutive turns, injects a note into the next turn's instructions so the agent offers a showroom callback via `capture_lead`.
- **Lead scoring:** the maximum `purchase_intent` in the call is stored on the booking or lead and shown in the frontend panel (hot / warm / cold).
- **Competitor / out-of-scope:** logged and used for analytics only. The prompt handles the reply; Jev does not override it.

`JEV_MODE=shadow`: run every question, log the results, and change nothing. `off`: no calls.

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
  model       text not null,          -- pinned Jev version or 'llm-fallback'
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

Compare Jev against `llm_fallback.py` (same questions through the LLM's structured output) on accuracy, calibration, and latency.

| Gate | Target to enable |
|---|---|
| Confirmation gate | 0 false accepts on the 40 confirmation turns; ≤ 10 % unnecessary re-confirmations |
| Intent (analytics, escalation) | ≥ 90 % accuracy; p90 latency ≤ 500 ms |
| Frustration / purchase-intent scores | Agreement within ±1 of human label on ≥ 80 % |

If Jev misses a target, keep that question in `shadow` or switch it to `llm_fallback`.

---

## 21. LangGraph orchestration (Phase 8, future)

### 21.1 When LangGraph is worth adding

Native LiveKit handoffs cover the POC. LangGraph earns its cost if the product grows into: multi-step flows with branches (finance or EMI pre-qualification, exchange-vehicle valuation), human-in-the-loop interrupts (a sales manager approves a discount), durable state across calls (callbacks that resume a conversation), or parallel tool branches.

### 21.2 Design constraints

- Graph: `route → sales_node | booking_node → END`. Each node is a **streaming** LLM node with the same tools as §7.
- **No guardrail node.** Speech transforms stay in `tts_node` (§9). A node that post-processes the full reply forces buffering and erases streaming.
- Router: `LG_ROUTER=jev` (Jev `intent` question mapped to a node), `rules` (sticky state: stay in the current node unless a handoff tool fired), or `llm`. Prefer `jev` or `rules`.
- Checkpointer: in-memory per call in the hot path. Persist to Postgres only at call end (or asynchronously), never synchronously per turn.
- Wire into LiveKit through its LangChain/LangGraph adapter (verify the current API) so STT, TTS, VAD, and turn detection stay unchanged.

### 21.3 Latency A/B

These are **estimates to check, not measurements**. Baseline is the native setup from Phase 1–6 (target p50 TTFA ≈ 1.0–1.2 s).

| Config | Added per-turn latency (estimate) | Why |
|---|---|---|
| Native LiveKit handoffs | 0 (baseline) | Handoff costs an LLM call only on the turn that switches agent |
| LangGraph + `rules` router | ~5–50 ms | Graph execution and adapter overhead only |
| LangGraph + `jev` router | ~70–500 ms | One Jev call before the main LLM, per vendor latency range |
| LangGraph + `llm` router | ~300–900 ms | A full non-streamed structured LLM call before the main LLM |
| Any config + buffering guardrail node | +0.5–2 s more | Waits for the whole reply before TTS starts |

Benchmark: `uv run python -m evals.latency_ab --configs native,lg_rules,lg_jev,lg_llm`. Run the 25 scenarios × 3 repetitions per config through the text harness, plus 10 live audio calls per config. Report p50/p90 TTFA, routing accuracy, and task success. Adopt LangGraph only if p50 TTFA rises by ≤ 150 ms, or if a feature from §21.1 justifies the cost.

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

## 23. Laya decision model (alternative provider for §20)

### 23.1 What it is

Laya (Convai Innovations) is a decision model in the same class as Jev: given context and typed questions, it returns yes/no, choice, or score answers with probabilities. It is offered through Vercel's AI Gateway via the AI SDK evaluation API, a native HTTP API, and a **TypeSafe-compatible API**, with model IDs `convaiinnovations/laya` and `convaiinnovations/laya-free`. It is free on the gateway through **2026-10-31**; after that the `-free` ID stops serving and the standard ID is billed.

Latency is not published. Measure it before using Laya in the serial confirmation gate.

### 23.2 How it fits

- `decisions/laya_client.py` implements the same `DecisionModel` protocol as Jev: one request per turn carrying all questions from §20.2, returning `{answer, confidence, latency_ms}` per question.
- Selected with `DECISION_PROVIDER=laya`. Thresholds (§20.3), shadow/enforce modes, timeouts, and fail-safe behaviour are unchanged.
- Results are logged to `call_signals` with `model = LAYA_MODEL`, so Jev and Laya can be compared from the same table.
- Inside the LiveKit agent, it is used exactly like Jev: per-turn signals are fired without awaiting from `on_user_turn_completed`; the confirmation gate is awaited with a timeout inside `book_test_ride`.

### 23.3 Comparison plan (part of Phase 7)

Run Jev, Laya, and `llm_fallback` over the §20.5 labelled set (60 intent turns, 40 confirmation turns), ideally before 2026-10-31 while Laya is free.

| Metric | Measured per provider |
|---|---|
| Confirmation gate false accepts (target 0) | ✓ |
| Unnecessary re-confirmations (target ≤ 10 %) | ✓ |
| Intent accuracy per language | ✓ |
| Calibration (confidence vs. actual accuracy) | ✓ |
| p50 / p90 latency from India | ✓ |
| Cost per 1,000 calls after free period | ✓ |

Pick one provider for `enforce`; the other can stay in `shadow` as a cross-check. Record the decision and numbers in this section.

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

Self-hosting covers transport and storage only. These remain cloud APIs: **Sarvam** STT/TTS, the **Azure OpenAI** LLM, **Jev/Laya**, and **Google Calendar** (the calendar mirror can be switched off with `CALENDAR_SYNC=off`). A fully offline agent would need local STT, TTS and LLM models, which is out of scope.

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