import random

import pytest

from honda_voice.agent.text_transforms import (
    InternalTagFilter,
    SpeechCleaner,
    TransformStats,
    clean_text_stream,
)


def run(text: str, lang: str = "hi-IN", chunks: list[str] | None = None) -> tuple[str, TransformStats]:
    stats = TransformStats()
    cleaner = SpeechCleaner(lang, stats)
    out = "".join(cleaner.feed(c) for c in (chunks if chunks is not None else [text])) + cleaner.flush()
    return out, stats


def spoken(text: str) -> str:
    """What TTS effectively receives: whitespace differences don't change speech."""
    return " ".join(text.split())


# Realistic LLM slips the transforms must clean up, in several languages.
SAMPLES = [
    "**Activa 125** ka ex-showroom price ₹80,600 hai 😊. Mileage 50 km/l, engine 124cc.\n"
    "Details: https://www.honda2wheelersindia.com/activa125 dekhiye!\n- Smart key\n  - LED headlamp\n"
    "1. Test ride book karein?",
    "Activa 125-இன் விலை ₹ 1.52 lakh ரூபாய். 👍🏽 #Honda Shine 100 mileage 65 kmpl!",
    "అవును, CB350 ధర Rs. 2,10,000 రూపాయలు. ఇంకా `test ride` కావాలా? 👨‍👩‍👧",
    "Unicorn ന്‍റെ വില ₹1,19,000 ആണ്; അവന്‍ ready ആണ്. ✅ www.example.com",
    "The *Hornet 2.0* costs ₹1.5 lakh rupees and returns about 45 km/litre. Shall I book? 🏍️",
]


@pytest.mark.parametrize("text", SAMPLES)
def test_every_single_split_point_gives_the_same_speech(text):
    whole, _ = run(text)
    for i in range(len(text) + 1):
        out, _ = run(text, chunks=[text[:i], text[i:]])
        assert spoken(out) == spoken(whole), f"split at {i}: {text[:i]!r} | {text[i:]!r}"


@pytest.mark.parametrize("text", SAMPLES)
def test_char_by_char_and_random_chunks_give_the_same_speech(text):
    whole, whole_stats = run(text)
    out, stats = run(text, chunks=list(text))
    assert spoken(out) == spoken(whole)
    assert stats.words == whole_stats.words and stats.violations == whole_stats.violations
    rng = random.Random(42)
    for _ in range(50):
        cuts = sorted(rng.sample(range(1, len(text)), k=min(8, len(text) - 1)))
        parts = [text[a:b] for a, b in zip([0, *cuts], [*cuts, len(text)], strict=True)]
        assert spoken(run(text, chunks=parts)[0]) == spoken(whole)


def test_streams_without_waiting_for_the_whole_reply():
    cleaner = SpeechCleaner("hi-IN")
    assert cleaner.feed("Namaste! Activa 125 ") == "Namaste! Activa 125 "
    assert cleaner.feed("ka mile") == "ka "  # complete words go out at once; only "mile" is held
    assert cleaner.feed("age ") == "mileage "


def test_holds_only_a_pending_currency_amount():
    cleaner = SpeechCleaner("hi-IN")
    assert cleaner.feed("Price ₹ ") == "Price "
    assert cleaner.feed("80,600 ") == ""
    assert cleaner.feed("hai. ") == "80 hazaar 6 sau rupaye hai. "


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("**Activa** is _great_", "Activa is great"),
        ("# Heading\n- one\n* two\n• three\n1. four\n2) five", "Heading\none\ntwo\nthree\nfour\nfive"),
        ("Activa-125 and 5 - 3 stay", "Activa-125 and 5 - 3 stay"),  # hyphens mid-line are not bullets
        ("See https://honda.in/x or www.honda.in now", "See or now"),
        (
            "50 km/l or 60 KMPL or 45 km/litre",
            "50 kilometre per litre or 60 kilometre per litre or 45 kilometre per litre",
        ),
        ("124cc engine, 350 cc too, cc alone", "124 C C engine, 350 C C too, C C alone"),
        ("according to the accent", "according to the accent"),  # 'cc' inside words untouched
        ("Great choice 😊👍🏽!", "Great choice !"),
    ],
)
def test_individual_rules(text, expected):
    assert spoken(run(text)[0]) == spoken(expected)


def test_indic_zero_width_joiners_are_kept():
    malayalam = "അവന്‍ ready"  # chillu written with ZWJ
    assert "‍" in run(malayalam, "ml-IN")[0]
    assert "‍" not in run("family 👨‍👩‍👧 photo", "en-IN")[0]


@pytest.mark.parametrize(
    ("lang", "text", "expected"),
    [
        ("hi-IN", "Price ₹80,600 hai", "Price 80 hazaar 6 sau rupaye hai"),
        ("hi-IN", "Price ₹80,600 rupaye hai", "Price 80 hazaar 6 sau rupaye hai"),  # no 'rupaye rupaye'
        ("hi-IN", "Rs. 1,45,000 only", "1 lakh 45 hazaar rupaye only"),
        ("en-IN", "It costs ₹1.5 lakh rupees.", "It costs 1 lakh 50 thousand rupees."),
        ("en-IN", "INR 72,150 total", "about 72,200 rupees total"),
        ("ta-IN", "விலை ₹ 80,600 ரூபாய்.", "விலை 80,600 ரூபாய்."),
        ("te-IN", "ధర ₹1,45,300", "ధర సుమారు 1,45,000 రూపాయలు"),
        ("ml-IN", "വില ₹80k", "വില 80,000 രൂപ"),
        ("kn-IN", "ಬೆಲೆ ₹42.82 lakh", "ಬೆಲೆ 42,82,000 ರೂಪಾಯಿ"),
    ],
)
def test_currency_backstop(lang, text, expected):
    out, stats = run(text, lang)
    assert spoken(out).endswith(spoken(expected).split(" ", 1)[1]) or spoken(out) == spoken(expected)
    assert "₹" not in out and "Rs." not in out
    assert len(stats.violations) == 1


def test_prices_from_tools_are_not_violations():
    out, stats = run("Activa 125 ka ex-showroom price 80 hazaar 6 sau rupaye hai.")
    assert stats.violations == []
    assert out == "Activa 125 ka ex-showroom price 80 hazaar 6 sau rupaye hai."


def test_never_truncates_and_counts_words():
    long_reply = " ".join(["shabd"] * 120)
    out, stats = run(long_reply)
    assert out == long_reply
    assert stats.words == 120


async def test_async_stream_adapter():
    async def chunks():
        for c in ["**Shine** ", "100 ka price ", "₹65,", "640 hai 😊"]:
            yield c

    stats = TransformStats()
    pieces = [p async for p in clean_text_stream(chunks(), "hi-IN", stats)]
    assert len(pieces) > 1  # streamed, not one final blob
    assert spoken("".join(pieces)) == "Shine 100 ka price lagbhag 65 hazaar 6 sau rupaye hai"
    assert len(stats.violations) == 1


def test_echoed_internal_tag_dropped_to_line_end_in_any_chunking():
    text = "Shall I confirm this test ride? [caller_language] English. Reply only in English.\nThanks"
    for size in (1, 3, 7, len(text)):
        c = SpeechCleaner("en-IN")
        out = "".join(c.feed(text[i : i + size]) for i in range(0, len(text), size)) + c.flush()
        # the space before the tag may already have been streamed out: harmless for TTS
        assert out.replace(" \n", "\n") == "Shall I confirm this test ride?\nThanks"
        assert c.stats.violations == ["internal tag spoken: [caller_language]"]


def test_llm_stream_tag_filter_keeps_history_clean():
    text = "Mobile number batayein. [caller_language] Hindi. Reply only in Hindi.\nAur kuch?"
    for size in (1, 4, len(text)):
        f = InternalTagFilter()
        out = "".join(f.feed(text[i : i + size]) for i in range(0, len(text), size)) + f.flush()
        assert out.replace(" \n", "\n") == "Mobile number batayein.\nAur kuch?"
        assert f.removed == ["internal tag spoken: [caller_language]"]


def test_number_hyphen_word_split_so_tts_reads_it():
    for size in (1, 4, 100):
        text = "Please share your 10-digit mobile number. CB350-RS. Phone 98765-43210, code HND-7K3Q9."
        c = SpeechCleaner("en-IN")
        out = "".join(c.feed(text[i : i + size]) for i in range(0, len(text), size)) + c.flush()
        assert "10 digit" in out and "10-digit" not in out
        assert "98765-43210" in out  # digits on both sides: untouched


def test_raw_booking_code_is_spelled_like_the_tool():
    from honda_voice.db.bookings import speak_reference

    for size in (1, 3, 100):
        text = "Your booking code is HND-W8X8D. Please bring your licence."
        c = SpeechCleaner("en-IN")
        out = "".join(c.feed(text[i : i + size]) for i in range(0, len(text), size)) + c.flush()
        assert speak_reference("HND-W8X8D") in out and "HND-" not in out
