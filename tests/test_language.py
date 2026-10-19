import pytest

from honda_voice.domain.language import (
    LANGUAGES,
    llm_language_tag,
    next_language,
    requested_language,
    script_language,
    script_mismatch,
    to_supported,
)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("ta-IN", "ta-IN"),
        ("ta", "ta-IN"),
        ("TA-in", "ta-IN"),
        ("en-US", "en-IN"),
        ("fr-FR", None),
        (None, None),
        ("", None),
    ],
)
def test_to_supported(code, expected):
    assert to_supported(code) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Tamil", "ta-IN"),
        ("English mein bolo", "en-IN"),
        ("can we talk in english please", "en-IN"),
        ("Tamil la pesunga", "ta-IN"),
        ("தமிழ்ல பேசுங்க", "ta-IN"),
        ("తెలుగులో మాట్లాడండి", "te-IN"),
        ("ಕನ್ನಡದಲ್ಲಿ ಮಾತಾಡಿ", "kn-IN"),
        ("മലയാളത്തിൽ സംസാരിക്കാമോ", "ml-IN"),
        ("हिंदी में बात करो", "hi-IN"),
        ("Hindi", "hi-IN"),
        ("Activa 125 ka price kya hai", None),
        ("Tamil, English, Hindi", None),  # caller repeats the list: ambiguous
    ],
)
def test_requested_language(text, expected):
    assert requested_language(text) == expected


@pytest.mark.parametrize(
    ("current", "transcript", "detected", "prob", "expected"),
    [
        # Short answers never flip the language, even with probability 1.0 (what Sarvam reports).
        ("ta-IN", "ok", "en-IN", 1.0, "ta-IN"),
        ("ta-IN", "haan ok", "hi-IN", 1.0, "ta-IN"),
        ("te-IN", "yes", "en-IN", 1.0, "te-IN"),
        # Three or more words in another language with a confident detection switch.
        ("ta-IN", "Activa 125 ka price kya hai", "hi-IN", 1.0, "hi-IN"),
        ("hi-IN", "What is the mileage of Shine", "en-IN", 0.95, "en-IN"),
        ("ta-IN", "Activa 125 mileage ಎಷ್ಟು ಇದೆ", "kn-IN", None, "kn-IN"),  # no probability reported
        # Low-confidence detection doesn't switch.
        ("ta-IN", "Activa 125 ka price kya hai", "hi-IN", 0.4, "ta-IN"),
        # Same language, unsupported language, or no detection: stay.
        ("ta-IN", "Activa 125 mileage எவ்வளவு", "ta-IN", 1.0, "ta-IN"),
        ("ta-IN", "quel est le prix de la moto", "fr-FR", 1.0, "ta-IN"),
        ("ta-IN", "some words here", None, None, "ta-IN"),
        # Explicit request wins, even when short or when detection disagrees.
        ("ta-IN", "English", "ta-IN", 1.0, "en-IN"),
        ("en-IN", "Telugu lo cheppandi", "en-IN", 1.0, "te-IN"),
    ],
)
def test_next_language(current, transcript, detected, prob, expected):
    assert next_language(current, transcript, detected, prob) == expected


def test_next_language_respects_supported_subset():
    assert next_language("hi-IN", "Tamil", "ta-IN", 1.0, supported=("hi-IN", "en-IN")) == "hi-IN"
    assert (
        next_language("hi-IN", "naan Tamil pesuven ok", "ta-IN", 1.0, supported=("hi-IN", "en-IN")) == "hi-IN"
    )


def test_llm_language_tag():
    assert (
        llm_language_tag("hi-IN")
        == "[caller_language] Hindi. Reply only in Hindi, written in Roman script (Hinglish)."
    )
    assert (
        llm_language_tag("te-IN")
        == "[caller_language] Telugu. Reply only in Telugu, written in Telugu script."
    )


@pytest.mark.parametrize(
    ("code", "reply", "mismatch"),
    [
        ("ta-IN", "Activa 125 mileage சுமார் 50 km per litre ஆகும்.", False),
        ("kn-IN", "Activa 125 mileage ಸುಮಾರು 50 km per litre ಇದೆ.", False),
        # Telugu reply in Roman script, as gpt-5.4-mini produced on 2026-10-15.
        ("te-IN", "Activa 125 mileage about 50 km per litre ani claim chestunnaru.", True),
        ("ta-IN", "Activa 125 का mileage 50 km है।", True),  # Devanagari in a Tamil reply
        ("hi-IN", "Activa 125 ka mileage lagbhag 50 km per litre hai.", False),
        ("hi-IN", "Activa 125 का mileage लगभग 50 km है।", True),  # Hinglish must be Roman script
        ("en-IN", "The Activa 125 gives about 50 km per litre.", False),
        ("en-IN", "Hornet 2.0 ka on-road Delhi mein about 1 lakh 75 thousand rupees hai.", True),  # step 10
        ("en-IN", "Would you like the price? I can check that for you.", False),
        ("ml-IN", "Activa 125?", False),  # too short to judge
    ],
)
def test_script_mismatch(code, reply, mismatch):
    assert (script_mismatch(code, reply) is not None) is mismatch


def test_every_language_names_itself():
    for code, info in LANGUAGES.items():
        assert requested_language(info.name) == code


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Activa 125 விலை என்ன?", "ta-IN"),
        ("Shine 100 ధర ఎంత?", "te-IN"),
        ("ಬೆಲೆ ಎಷ್ಟು", "kn-IN"),
        ("വില എത്രയാണ്", "ml-IN"),
        ("Activa का price क्या है", "hi-IN"),
        ("What is the price", None),  # Latin: English or Hinglish, can't tell
    ],
)
def test_script_language(text, expected):
    assert script_language(text) == expected


def test_next_language_infers_from_script_when_detection_missing():
    assert next_language("en-IN", "Activa 125 விலை என்ன சொல்லுங்க", None, None) == "ta-IN"
    assert (
        next_language("ta-IN", "What is the price of the Activa", None, None) == "ta-IN"
    )  # Latin: no switch


def test_is_language_pick():
    from honda_voice.domain.language import is_language_pick

    for pick in ("Tamil", "தமிழ்", "English please", "Hindi mein baat karo", "తెలుగు"):
        assert is_language_pick(pick), pick
    for other in ("hello", "", "Tamil English", "can you please tell me in Tamil the price of Activa"):
        assert not is_language_pick(other), other
