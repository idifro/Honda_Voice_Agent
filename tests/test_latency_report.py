import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "latency_report", Path(__file__).resolve().parents[1] / "scripts" / "latency_report.py"
)
lr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lr)


def test_percentile_nearest_rank():
    assert lr.percentile([1.0, 2.0, 3.0, 4.0, 10.0], 0.5) == 3.0
    assert lr.percentile([1.0, 2.0, 3.0, 4.0, 10.0], 0.9) == 10.0
    assert lr.percentile([], 0.5) is None


def test_turn_types():
    assert lr.turn_type({"ttfa_s": 0.9}) == "reply from code"
    assert lr.turn_type({"ttfa_s": 1.5, "llm_calls": 1}) == "LLM, no tool"
    assert lr.turn_type({"ttfa_s": 2.5, "llm_calls": 2, "tools": ["find_bike"]}) == "catalog tool"
    assert lr.turn_type({"ttfa_s": 3.0, "llm_calls": 2, "tools": ["book_test_ride"]}) == "booking / handoff"


def test_report_against_targets(tmp_path):
    rows = [
        {
            "room": "honda-a",
            "turn": i,
            "ttfa_s": t,
            "llm_calls": 1,
            "lang": "ta-IN",
            "reply_lang": "ta-IN",
            "words": 20,
            "over_length": False,
            "violations": [],
            "at": "2026-10-16T10:00:00+00:00",
        }
        for i, t in enumerate([1.4, 1.5, 1.6, 1.7, 2.6], 1)
    ] + [{"room": "honda-a", "turn": 6, "violations": [], "at": "2026-10-16T10:01:00+00:00"}]  # no reply
    path = tmp_path / "turns.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows))
    text, ok = lr.report(lr.load(path, None, None, None))
    assert ok and "| TTFA p50 ≤ 2.0 s | met | 1.60 s (stretch ≤ 1.5 s met) |" not in text
    assert "| TTFA p50 ≤ 2.0 s | met | 1.60 s |" in text and "| TTFA p90 ≤ 3.0 s | met | 2.60 s |" in text
    assert "Turns without an agent reply: 1 of 6" in text
    assert lr.load(path, "2026-10-17", None, None) == []
