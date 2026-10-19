"""Per-turn latency and speech metrics (SPEC §16). One JSON line per caller turn in logs/turns.jsonl,
which scripts/latency_report.py aggregates later. No transcripts or phone numbers are logged here."""

import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from livekit.agents import metrics as lk_metrics

from honda_voice.agent.text_transforms import TransformStats

logger = logging.getLogger("honda_voice.metrics")
DEFAULT_LOG_PATH = Path("logs/turns.jsonl")
OVER_LENGTH_WORDS = 40


class TurnMetrics:
    """Collects one record per caller turn: time to first audio (TTFA) plus component latencies."""

    def __init__(self, room: str, log_path: Path | None = DEFAULT_LOG_PATH) -> None:
        self.room = room
        self.log_path = log_path
        self._turn = 0
        self._rec: dict[str, Any] | None = None
        self._open_replies: dict[int, int] = {}  # id(rec) -> replies still speaking
        self._waiting: dict[int, dict[str, Any]] = {}  # turns ended whose reply is still speaking

    def attach(self, session: Any) -> None:
        session.on("user_state_changed", self._on_user_state)
        session.on("agent_state_changed", self._on_agent_state)
        session.on("metrics_collected", self._on_metrics)
        session.on("function_tools_executed", self._on_tools)
        session.on("close", lambda _ev: self.close())

    # --- session events ---------------------------------------------------------------------------
    def _on_user_state(self, ev: Any) -> None:
        if ev.old_state == "speaking" and ev.new_state != "speaking":
            self.flush()
            self._turn += 1
            self._rec = {"room": self.room, "turn": self._turn, "user_stopped_at": _ts(ev), "violations": []}

    def _on_agent_state(self, ev: Any) -> None:
        rec = self._rec
        if rec is not None and ev.new_state == "speaking" and "ttfa_s" not in rec:
            rec["ttfa_s"] = round(_ts(ev) - rec["user_stopped_at"], 3)

    def _on_metrics(self, ev: Any) -> None:
        rec, m = self._rec, ev.metrics
        if rec is None:
            return
        if isinstance(m, lk_metrics.EOUMetrics):
            rec.setdefault("eou_delay_s", round(m.end_of_utterance_delay, 3))
            rec.setdefault("transcription_delay_s", round(m.transcription_delay, 3))
        elif isinstance(m, lk_metrics.LLMMetrics):
            if m.cancelled:  # discarded preemptive attempts: counted, but not as answering calls
                rec["llm_cancelled"] = rec.get("llm_cancelled", 0) + 1
                return
            rec.setdefault("llm_ttft_s", round(m.ttft, 3))  # first completed LLM call of the turn
            rec.setdefault("llm_prompt_tokens", m.prompt_tokens)
            rec.setdefault("llm_cached_tokens", m.prompt_cached_tokens)
            rec["llm_calls"] = rec.get("llm_calls", 0) + 1
        elif isinstance(m, lk_metrics.TTSMetrics):
            rec.setdefault("tts_ttfb_s", round(m.ttfb, 3))
        elif isinstance(m, lk_metrics.STTMetrics):
            rec["stt_audio_s"] = round(rec.get("stt_audio_s", 0) + m.audio_duration, 3)

    def _on_tools(self, ev: Any) -> None:
        if self._rec is not None:
            self._rec.setdefault("tools", []).extend(c.name for c in ev.function_calls)

    # --- called by the agent ------------------------------------------------------------------------
    def record_language(self, lang: str, switched: bool) -> None:
        if self._rec is not None:
            self._rec["lang"] = lang
            self._rec["lang_switched"] = switched

    def start_reply(self, lang: str) -> dict[str, Any] | None:
        """Called when a reply starts speaking: the reply belongs to the current turn. Returns that turn's
        record for finish_reply. (Before 2026-10-16 the language was recorded when the reply ended, so a
        reply that finished after the caller's next turn began was filed under the wrong turn.)"""
        rec = self._rec
        if rec is not None:
            rec.setdefault("reply_lang", lang)
            self._open_replies[id(rec)] = self._open_replies.get(id(rec), 0) + 1
        return rec

    def finish_reply(self, rec: dict[str, Any] | None, stats: TransformStats) -> None:
        """Add the reply's words and violations to the turn it started in (even if a new turn has begun)."""
        if rec is None:
            return
        rec["words"] = rec.get("words", 0) + stats.words
        rec["over_length"] = rec["words"] > OVER_LENGTH_WORDS
        rec["violations"].extend(stats.violations)
        left = self._open_replies.get(id(rec), 1) - 1
        if left > 0:
            self._open_replies[id(rec)] = left
            return
        self._open_replies.pop(id(rec), None)
        if id(rec) in self._waiting:  # the turn already ended: write it now that its reply is complete
            self._write(self._waiting.pop(id(rec)))

    def flush(self) -> None:
        """End the current turn. Its line is written once its reply (if any is still speaking) finishes."""
        rec, self._rec = self._rec, None
        if rec is None:
            return
        if self._open_replies.get(id(rec)):
            self._waiting[id(rec)] = rec
            return
        self._write(rec)

    def close(self) -> None:
        """Call end: write everything, including turns whose reply never reported back."""
        self.flush()
        for rec in list(self._waiting.values()):
            self._write(rec)
        self._waiting.clear()
        self._open_replies.clear()

    def _write(self, rec: dict[str, Any]) -> None:
        rec.pop("user_stopped_at", None)
        rec.setdefault("at", datetime.now(UTC).isoformat(timespec="seconds"))  # for latency_report --since
        line = json.dumps(rec, ensure_ascii=False)
        logger.info("turn %s", line)
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


def _ts(ev: Any) -> float:
    return getattr(ev, "created_at", None) or time.time()
