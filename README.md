# Honda 2-Wheeler Voice Assistant (POC)

A browser-based voice agent for a Honda two-wheeler showroom. Callers speak **Tamil, English, Hindi (Hinglish), Telugu, Malayalam or Kannada**; the agent replies in the caller's language, answers questions about models, prices, specs and showroom hours, suggests models for the caller's needs with demo EMI examples, books or cancels test rides, and records callback requests (with consent) as scored leads. Bookings are stored in Postgres (Supabase) and copied to a shared Google Calendar.

- How it works: [docs/architecture.md](docs/architecture.md) (components, models, workflows, diagrams)
- Full specification and decision history: [docs/SPEC.md](docs/SPEC.md)
- Rules for working on the code: [CLAUDE.md](CLAUDE.md)
- Demo call scripts and the use cases you can show: [docs/DEMO.md](docs/DEMO.md)

## What you need

**Software**
- [uv](https://docs.astral.sh/uv/) (installs Python 3.11 and the Python dependencies)
- Node.js 20 or newer (includes `corepack`, which runs the pinned `pnpm`)
- A browser with a microphone (Chrome or Edge recommended)

**Accounts and services**

| Service | What to create | Goes into |
|---|---|---|
| [LiveKit Cloud](https://cloud.livekit.io) | A project; its URL, API key and secret | `.env` and `frontend/.env.local` |
| [Sarvam AI](https://www.sarvam.ai) | An API key (speech-to-text `saaras:v4`, text-to-speech `bulbul:v4-flash`) | `.env` |
| Azure OpenAI | A resource with a `gpt-5.4-mini` deployment (and optionally a fallback deployment, e.g. `gpt-5.4-nano`) | `.env` |
| [Supabase](https://supabase.com) | A project (we use Mumbai, `ap-south-1`); the **session pooler** connection string | `.env` |
| Google Cloud + Google Calendar | A service account with the Calendar API enabled, and a calendar shared with it (free; no billing account needed). Optional: set `CALENDAR_SYNC=off` to skip | `secrets/` and `.env` |

## Set up

```bash
# 1. Python dependencies
uv sync

# 2. Backend configuration: copy the template and fill in the values (never commit .env)
cp .env.example .env
chmod 600 .env

# 3. Database: create the tables, functions and roles, then load the catalog and test-ride slots
uv run python scripts/apply_migrations.py
uv run python scripts/seed_catalog.py
uv run python scripts/generate_slots.py --days 14       # re-run any time to extend; existing slots are kept

# 4. Frontend configuration
cp frontend/.env.local.example frontend/.env.local
chmod 600 frontend/.env.local
#    fill in the LiveKit values, DEMO_PASSCODE and SESSION_SECRET
#    (python -c "import secrets; print(secrets.token_urlsafe(48))"), then create the
#    read-only database role's password (it writes DATABASE_URL into frontend/.env.local):
uv run python scripts/set_frontend_db_password.py

# 5. Frontend dependencies
cd frontend && corepack pnpm@9.15.9 install && cd ..
```

**Google Calendar (optional)**
1. In the Google Cloud console: create a project, enable the **Google Calendar API**, create a **service account**, and download a JSON key into `secrets/` (git-ignored; `chmod 600`).
2. In Google Calendar: create a calendar (e.g. "Honda Demo – Test Rides", time zone India Standard Time) and share it with the service account's email with **Make changes to events**.
3. In `.env`: `CALENDAR_SYNC=on`, `GOOGLE_CALENDAR_ID=<the calendar's ID>`, `GOOGLE_SERVICE_ACCOUNT_JSON=secrets/<key file>.json`.
4. Check it: `uv run python scripts/check_calendar.py` (creates, reads and deletes a test event).

## Run

Two terminals:

```bash
uv run python -m honda_voice.agent.worker dev           # the agent, connected to LiveKit Cloud
cd frontend && corepack pnpm@9.15.9 dev                 # the website: http://localhost:3000
```

Open http://localhost:3000, enter the demo passcode, press **Start call** and allow the microphone. The agent greets you in Tamil and asks which language you'd like to use.

Without the website, you can talk to the agent in the terminal: `uv run python -m honda_voice.agent.worker console`.

## Check before a demo

```bash
make test                             # quick: lint, Python unit tests, frontend tests (no database, no network)
make help                             # every target: test-integration, evals, signals, check, …
scripts/check_all.sh                  # lint, unit, database and frontend tests, and the scripted conversations (~15 min)
SKIP_EVALS=1 scripts/check_all.sh     # the same without the conversations (~2 min)
uv run python scripts/latency_report.py --since 2026-10-16 --room honda-   # response times from real browser calls
```

`scripts/check_all.sh` prints a pass/fail summary. The scripted conversations use the real LLM and database and must pass at least 95 % (LLM replies vary slightly between runs); their bookings are cleaned up afterwards.

## Privacy notice

This notice is shown on the sign-in screen and before each call:

- Your voice is processed live to understand you and reply: speech recognition and speech by **Sarvam AI**, replies by **Azure OpenAI**; the audio travels through **LiveKit Cloud**. Calls are **not recorded**.
- If you book a test ride, we store your **name, mobile number, chosen slot, call language and your answer about a driving licence**, only to arrange the ride, in a database hosted by **Supabase** (Mumbai). A copy of the booking with the number **masked** goes to the showroom's **Google Calendar**.
- To help you choose, the assistant notes what you're looking for (for example use, budget, timeline) as categories, never your words, and keeps a short summary of the call.
- The showroom calls you back only if you agree when the assistant reads your number back; we then store your name, number, language and that consent (with its time), only for that callback.
- Phone numbers are masked in logs and on screen. This is a demo: please don't share anything you wouldn't want a showroom to see.

Personal data here is covered by India's Digital Personal Data Protection (DPDP) Act. To remove a booking, cancel it in a call (booking code + phone number) or ask the operator to delete it from the `bookings` table.

## Troubleshooting

| Symptom | Where to look |
|---|---|
| A call hangs or behaves oddly | `logs/worker.log` (INFO, one JSON line per event, filter by `room`) and `logs/turns.jsonl` (one line per caller turn with timings). Both are git-ignored. |
| "Not signed in" / 401 in the browser | The session cookie expired (2 hours) or `SESSION_SECRET` changed: sign in again. |
| The agent never joins the call | Is the worker running and registered (`registered worker` in its log)? Does `AGENT_NAME` in `frontend/.env.local` match `honda-sales`? Each worker takes at most `MAX_CONCURRENT_CALLS` calls. |
| No bookings in the panel | `uv run python scripts/set_frontend_db_password.py` (resets the read-only role and its URL); check `DATABASE_CA_CERT` points to `certs/supabase-prod-ca-2021.crt`. |
| Calendar events missing | `uv run python scripts/check_calendar.py`, then `uv run python scripts/retry_calendar_sync.py` to push failed or pending bookings. |
| Azure errors or slow replies | With `AZURE_OPENAI_FALLBACK_DEPLOYMENT` set, the agent switches to the fallback automatically; the log shows "switching to next LLM". |

## Repository layout

```text
src/honda_voice/     the agent: agent/ (agents, prompts, speech cleanup, metrics, worker), domain/ (pure logic),
                     db/ (queries, bookings, calendar sync), integrations/ (Google Calendar), config.py
frontend/            the website (Next.js, from LiveKit's agent-starter-react template)
data/                catalog JSON and database migrations
scripts/             setup and maintenance scripts, latency report, pre-demo check
evals/               scripted conversations (scenarios/*.yaml) and their runner
tests/               unit tests; tests/integration/ needs the database
docs/                SPEC.md, architecture.md (+ diagrams/), DEMO.md
certs/               Supabase's public root CA (database TLS verification)
```
