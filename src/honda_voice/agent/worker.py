"""Worker entrypoint (SPEC §3, §4). Run: `uv run python -m honda_voice.agent.worker console` (or `dev`)."""

import asyncio
import logging

from dotenv import load_dotenv

load_dotenv(".env")  # same file Settings reads; the LiveKit plugins read os.environ

from livekit import rtc  # noqa: E402
from livekit.agents import (  # noqa: E402
    AgentServer,
    AgentSession,
    BackgroundAudioPlayer,
    JobContext,
    JobProcess,
    cli,
)
from livekit.agents.inference import TurnDetector  # noqa: E402
from livekit.agents.voice.agent_session import SessionConnectOptions  # noqa: E402
from livekit.plugins import sarvam, silero  # noqa: E402

from honda_voice.agent.call_state import CallState  # noqa: E402
from honda_voice.agent.hold import HoldAudio  # noqa: E402
from honda_voice.agent.metrics import TurnMetrics  # noqa: E402
from honda_voice.agent.prompts import CALL_LIMIT_LINE  # noqa: E402
from honda_voice.agent.sales_agent import SalesAgent  # noqa: E402
from honda_voice.config import build_llm, get_settings, llm_conn_options  # noqa: E402
from honda_voice.db.calendar_sync import CalendarSync  # noqa: E402
from honda_voice.db.catalog import Catalog  # noqa: E402
from honda_voice.db.pool import get_pool  # noqa: E402

logger = logging.getLogger("honda_voice.worker")


def call_load(server: AgentServer) -> float:
    """Load = active calls / MAX_CONCURRENT_CALLS. At 1.0 LiveKit stops dispatching new calls to this worker,
    so a burst can't exceed the Azure/Sarvam quotas (they go to another worker, or wait)."""
    return len(server.active_jobs) / max(1, get_settings().max_concurrent_calls)


server = AgentServer(load_fnc=call_load, load_threshold=1.0)


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


async def end_call_after(seconds: float, session, ctx, state: CallState) -> None:
    """Cost limit (Phase 5): after `seconds`, say goodbye in the caller's language and close the room, which
    also disconnects the caller. A booking already confirmed is unaffected (it's in the database)."""
    await asyncio.sleep(seconds)
    logger.info("max call length reached (%.0f s): ending the call", seconds)
    try:
        handle = session.say(CALL_LIMIT_LINE[state.lang], allow_interruptions=False)
        await asyncio.wait_for(handle.wait_for_playout(), timeout=20)
    except Exception:  # the goodbye is best effort; ending the call is not
        logger.warning("call-limit goodbye failed", exc_info=True)
    try:
        await ctx.delete_room()
    except Exception:  # e.g. console mode has no real room
        logger.debug("delete_room failed", exc_info=True)
    ctx.shutdown(reason="max_call_duration")


@server.rtc_session(agent_name="honda-sales")
async def entrypoint(ctx: JobContext) -> None:
    s = get_settings()
    tts = sarvam.TTS(
        model=s.tts_model,
        speaker=s.tts_speaker,
        target_language_code=s.greeting_language,
        dict_id=s.sarvam_tts_dict_id,
        api_key=s.sarvam_api_key.get_secret_value(),
    )
    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        stt=sarvam.STT(
            model=s.stt_model,
            mode=s.stt_mode,
            language=s.stt_language,  # "unknown" = auto-detect (SPEC §4.1)
            api_key=s.sarvam_api_key.get_secret_value(),
            # Sarvam's default end-of-speech needs ~0.58 s of silence (18 of 24 frames); this preset needs
            # ~64 ms. Turn-taking is still decided by LiveKit's turn detector, not by Sarvam (step 6b).
            high_vad_sensitivity=True,
        ),
        llm=build_llm(s),
        # A stalled LLM request (no bytes for LLM_ATTEMPT_TIMEOUT_S) is abandoned and retried quickly; the
        # 10 s default froze a call on 2026-10-16. A fallback deployment (FallbackAdapter) applies its own.
        conn_options=SessionConnectOptions(llm_conn_options=llm_conn_options(s)),
        tts=tts,
        tts_text_transforms=None,  # our streaming cleanup in MultilingualAgent.tts_node is the only one
        turn_handling={
            # Cloud v1 detector; languages it doesn't support fall back to VAD silence (SPEC §4).
            "turn_detection": TurnDetector(),
            # Streaming-detector defaults are 0.3 / 2.5 s; console run 1 hit the 2.5 s cap on 4 of 9 turns.
            "endpointing": {"min_delay": 0.3, "max_delay": 1.5},
            "interruption": {"min_duration": 0.5},
            # LLM already runs preemptively by default; also start TTS before the turn is confirmed.
            "preemptive_generation": {"enabled": True, "preemptive_tts": True},
        },
    )
    turn_metrics = TurnMetrics(room=ctx.room.name if ctx.room else "console")
    turn_metrics.attach(session)
    pool = await get_pool()
    calendar = None
    if s.calendar_sync == "on":
        from honda_voice.integrations.gcalendar import CalendarClient

        calendar = CalendarSync(pool, CalendarClient(s.google_service_account_json, s.google_calendar_id))

        async def _finish_calendar() -> None:  # let this call's syncs finish; the rest stay PENDING
            await calendar.drain()
            await calendar.client.close()

        ctx.add_shutdown_callback(_finish_calendar)
    state = CallState(
        catalog=Catalog.load_in_background(pool),  # fills while the greeting plays; no DB trip mid-turn
        lang=s.greeting_language,
        supported=s.supported_languages,
        tts=tts,
        metrics=turn_metrics,
        pool=pool,
        showroom_id=s.showroom_id,
        room=ctx.room.name if ctx.room else None,
        calendar=calendar,
    )
    agent = SalesAgent(state)

    @session.on("user_input_transcribed")
    def _on_transcript(ev) -> None:
        if ev.language:  # interim or final: whichever arrives first, the latest detection wins
            state.detected = str(ev.language)  # shared, so it reaches whichever agent is active

    # record=False: no session recording upload until a retention policy is documented (SPEC §17).
    await session.start(agent=agent, room=ctx.room, record=False)

    limit = asyncio.create_task(end_call_after(s.max_call_minutes * 60, session, ctx, state))

    async def _cancel_limit() -> None:
        limit.cancel()

    ctx.add_shutdown_callback(_cancel_limit)
    await start_hold_audio(ctx, session, state, s)


async def start_hold_audio(ctx: JobContext, session: AgentSession, state: CallState, s) -> None:
    """The hold line on slow turns (agent/hold.py), on its own audio track."""
    player = BackgroundAudioPlayer()
    try:
        await player.start(room=ctx.room, agent_session=session)
    except Exception:  # e.g. console mode: the call works without hold audio
        logger.warning("hold audio unavailable", exc_info=True)
        return

    async def synthesize(text: str, lang: str) -> list[rtc.AudioFrame]:
        """The line as 48 kHz mono frames (the background player's mix format), via a Sarvam REST call."""
        tts = sarvam.TTS(
            model=s.tts_model,
            speaker=s.tts_speaker,
            target_language_code=lang,
            dict_id=s.sarvam_tts_dict_id,
            api_key=s.sarvam_api_key.get_secret_value(),
        )
        out: list[rtc.AudioFrame] = []
        resampler: rtc.AudioResampler | None = None
        try:
            async with tts.synthesize(text) as stream:
                async for ev in stream:
                    frame = ev.frame
                    if frame.sample_rate == 48000 and frame.num_channels == 1:
                        out.append(frame)
                        continue
                    resampler = resampler or rtc.AudioResampler(
                        frame.sample_rate, 48000, num_channels=frame.num_channels
                    )
                    out.extend(resampler.push(frame))
            if resampler is not None:
                out.extend(resampler.flush())
        finally:
            await tts.aclose()
        return out

    hold = HoldAudio(player, state, synthesize, after_s=s.hold_after_s)
    session.on("agent_state_changed", lambda ev: hold.on_agent_state(ev.new_state))
    state.on_language_switch = hold.prefetch
    hold.prefetch(state.lang)

    async def _close_hold() -> None:
        hold.stop()
        await player.aclose()

    ctx.add_shutdown_callback(_close_hold)


def add_log_file(path: str, level: int = logging.INFO) -> logging.Handler | None:
    """Also write logs to `path` (JSON lines, 10 MB x 5 files). Attached in the main process: LiveKit forwards
    every job process's log records there, so one file holds the worker and all calls."""
    if not path:
        return None
    from logging.handlers import RotatingFileHandler
    from pathlib import Path

    from livekit.agents.cli.log import JsonFormatter

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=10_000_000, backupCount=5, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(JsonFormatter())
    logging.getLogger().addHandler(handler)
    return handler


if __name__ == "__main__":
    add_log_file(get_settings().log_file)
    cli.run_app(server)
