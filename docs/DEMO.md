# Demo script (about 4 minutes)

A single call that shows the strongest features: language choice and switching, prices from the catalog, the competitor policy, a test-ride booking with the licence question and the read-back, the live bookings panel and calendar, and a cancellation.

The same conversation runs as a scripted test (`evals/scenarios/demo.yaml`) in the pre-demo check, so it is known to work with the current code. Verified 2026-10-16.

## Before the audience arrives (5 minutes)

1. `scripts/check_all.sh` → all **PASS** (or at least `SKIP_EVALS=1 scripts/check_all.sh` if short on time).
2. `uv run python scripts/generate_slots.py --days 14` (keeps tomorrow's slots available).
3. Start the agent: `uv run python -m honda_voice.agent.worker dev`; wait for `registered worker`.
4. Start the website: `cd frontend && corepack pnpm@9.15.9 dev`; open http://localhost:3000 and sign in.
5. Open the Google Calendar "Honda Demo – Test Rides" in a second tab.
6. Use a headset or a quiet room; speak at a normal pace and pause briefly after each sentence.

## The call

| # | You say | What the agent does | Point out |
|---|---|---|---|
| 0 | (press **Start call**) | Greets in Tamil and asks which language you'd like | Fixed greeting, no waiting for the AI |
| 1 | "Tamil" (or "தமிழ்") | Says what it can do, in Tamil | Played straight from code: no AI call, so it's instant |
| 2 | "Activa 125 விலை என்ன?" | Ex-showroom price range in Tamil | Prices are read from the catalog, never invented by the AI |
| 3 | "Can you tell me in English, what is the mileage of the Shine 100?" | Switches to English; gives the mileage | Language switches only on a clear request or a full sentence, never on "ok" |
| 4 | "Is the TVS Jupiter better than the Activa?" | Says nothing about the Jupiter; offers a Honda fact | Competitor policy: no claims about other brands |
| 5 | "I'd like to book a test ride for the Activa 125 tomorrow at 4 pm" | Confirms 4 pm is free; asks about a driving licence | Hands over to the booking agent; slot checked in the database |
| 6 | "Yes, I have a driving licence" | Asks your name | A ride can't be booked without a licence |
| 7 | "My name is …" | Asks for your mobile number | |
| 8 | Your mobile number, digit by digit | Reads back model, slot, name, number in 5 + 5 digits, licence; asks to confirm | One read-back built by code; nothing is booked yet |
| 9 | "Yes, please confirm" | Books it; spells the booking code "H N D, …"; reminds you to bring the licence | **Show the Bookings panel** (updates within 5 s, name and number masked) and **the calendar** (event within ~2 s) |
| 10 | "I'd like to cancel that booking" | Asks for the code and number | |
| 11 | The code (letter by letter), then the number | Cancels it | The panel shows *Cancelled*; the calendar event disappears |

Sales advisor (Phase 7), if time allows: "I need a scooter for my mother, easy to handle, good mileage" (two suggestions with reasons; watch the **Caller profile** card fill in), "too costly, can I pay in EMI?" (a demo EMI from code), then "please have the showroom call me" + name and number + "yes" (consent read-back; the **Leads** tab shows the scored lead after the call ends, with a summary; **Download CSV**).

Optional extras if time allows: "Hindi mein baat karo" (Hinglish replies), "When should my Activa be serviced?" (polite out-of-scope line with a callback offer), "Compare the Unicorn and the SP160", "Is the showroom open on Sunday at 6 pm?".

## If something goes wrong

| What happens | What to do |
|---|---|
| A short pause, then "One moment please, let me check." | Normal on a slow turn: the reply follows. |
| The agent answers in the wrong language | Say "please speak in English" (or the language you want). |
| It mishears the phone number | Say "no, that's wrong", then the number again, digit by digit; it reads it back again before booking. |
| The slot is taken | It offers the nearest free times; pick one, then confirm the new read-back. |
| No reply at all for more than ~10 s | Press the red end-call button and start a new call. Afterwards, check `logs/worker.log` for that room. |

## After the demo

- Response times from the call: `uv run python scripts/latency_report.py --last 1`.
- Rotate `DEMO_PASSCODE` in `frontend/.env.local` if it was shown to the audience.
