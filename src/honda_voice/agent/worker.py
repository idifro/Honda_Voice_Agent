"""Worker entrypoint (SPEC §3, §4). Run: `uv run python -m honda_voice.agent.worker console` (or `dev`)."""

import logging

from dotenv import load_dotenv

load_dotenv(".env")  # same file Settings reads; the LiveKit plugins read os.environ

from livekit.agents import AgentServer, AgentSession, JobContext, JobProcess, cli  # noqa: E402
from livekit.agents.inference import TurnDetector  # noqa: E402
from livekit.plugins import sarvam, silero  # noqa: E402

from honda_voice.agent.metrics import TurnMetrics  # noqa: E402
from honda_voice.agent.sales_agent import SalesAgent  # noqa: E402
from honda_voice.config import build_llm, get_settings  # noqa: E402
from honda_voice.db.catalog import Catalog  # noqa: E402
from honda_voice.db.pool import get_pool  # noqa: E402

logger = logging.getLogger("honda_voice.worker")
server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


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
        tts=tts,
        tts_text_transforms=None,  # our streaming cleanup in SalesAgent.tts_node is the only one
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
    # Loaded once per call, in the background while the greeting plays: alias matching, pre-fetch, list and
    # compare then need no database round trip mid-turn.
    catalog = Catalog.load_in_background(await get_pool())
    agent = SalesAgent(
        catalog=catalog,
        initial_language=s.greeting_language,
        supported_languages=s.supported_languages,
        tts=tts,
        turn_metrics=turn_metrics,
    )

    @session.on("user_input_transcribed")
    def _on_transcript(ev) -> None:
        if ev.language:  # interim or final: whichever arrives first, the latest detection wins
            agent.note_detected_language(str(ev.language))

    # record=False: no session recording upload until a retention policy is documented (SPEC §17).
    await session.start(agent=agent, room=ctx.room, record=False)


if __name__ == "__main__":
    cli.run_app(server)
