import json
from pathlib import Path

import pytest

from honda_voice.domain.pricing import PRICE_LANGUAGES, format_inr_spoken, group_indian, round_inr

CATALOG = Path(__file__).resolve().parents[1] / "data" / "catalog"


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (74_600, 74_600),
        (74_640, 74_600),
        (74_650, 74_700),  # half-up, not banker's rounding
        (99_949, 99_900),
        (99_950, 100_000),  # rounds across the lakh boundary
        (145_499, 145_000),
        (145_500, 146_000),
        (4_282_000, 4_282_000),
    ],
)
def test_round_inr(amount, expected):
    assert round_inr(amount) == expected


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (600, "600"),
        (80_600, "80,600"),
        (145_000, "1,45,000"),
        (4_282_000, "42,82,000"),
        (12_000_000, "1,20,00,000"),
    ],
)
def test_group_indian(n, expected):
    assert group_indian(n) == expected


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (74_600, "74 hazaar 6 sau rupaye"),
        (76_000, "76 hazaar rupaye"),
        (74_640, "lagbhag 74 hazaar 6 sau rupaye"),
        (65_640, "lagbhag 65 hazaar 6 sau rupaye"),  # Shine 100, cheapest in the catalog
        (99_950, "lagbhag 1 lakh rupaye"),
        (145_000, "1 lakh 45 hazaar rupaye"),
        (151_600, "lagbhag 1 lakh 52 hazaar rupaye"),  # Activa e: RoadSync Duo
        (200_000, "2 lakh rupaye"),
        (4_282_000, "42 lakh 82 hazaar rupaye"),  # Gold Wing, most expensive
        (12_000_000, "1 crore 20 lakh rupaye"),
    ],
)
def test_hindi(amount, expected):
    assert format_inr_spoken(amount, "hi-IN") == expected


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (74_600, "74,600 rupees"),
        (72_150, "about 72,200 rupees"),
        (145_000, "1 lakh 45 thousand rupees"),
        (200_000, "2 lakh rupees"),
        (151_600, "about 1 lakh 52 thousand rupees"),
    ],
)
def test_english(amount, expected):
    assert format_inr_spoken(amount, "en-IN") == expected


@pytest.mark.parametrize(
    ("lang", "exact", "approx"),
    [
        ("ta-IN", "80,600 ரூபாய்", "சுமார் 1,45,000 ரூபாய்"),
        ("te-IN", "80,600 రూపాయలు", "సుమారు 1,45,000 రూపాయలు"),
        ("ml-IN", "80,600 രൂപ", "ഏകദേശം 1,45,000 രൂപ"),
        ("kn-IN", "80,600 ರೂಪಾಯಿ", "ಸುಮಾರು 1,45,000 ರೂಪಾಯಿ"),
    ],
)
def test_numeral_languages(lang, exact, approx):
    assert format_inr_spoken(80_600, lang) == exact
    assert format_inr_spoken(145_300, lang) == approx


def test_approx_prefix_only_when_rounding_changed_value():
    for lang in PRICE_LANGUAGES:
        assert not format_inr_spoken(80_600, lang).startswith(("lagbhag", "about"))
        assert format_inr_spoken(80_640, lang) != format_inr_spoken(80_600, lang)


@pytest.mark.parametrize("bad", [0, -5, 99])
def test_rejects_implausible_amounts(bad):
    with pytest.raises(ValueError):
        format_inr_spoken(bad, "hi-IN")


@pytest.mark.parametrize("bad", [80_600.0, "80600", True])
def test_rejects_non_int(bad):
    with pytest.raises(TypeError):
        format_inr_spoken(bad, "hi-IN")


@pytest.mark.parametrize("lang", ["hi", "fr-FR", "", "unknown"])
def test_rejects_unsupported_language(lang):
    with pytest.raises(ValueError):
        format_inr_spoken(80_600, lang)


def test_every_catalog_price_formats_in_every_language():
    prices = set()
    for f in CATALOG.glob("*.json"):
        if f.name.startswith("_"):
            continue
        model = json.loads(f.read_text())
        prices |= {v["ex_showroom_inr"] for v in model["variants"] if v["ex_showroom_inr"]}
        prices |= {model["price"][k] for k in ("min", "max") if model["price"][k]}
    assert len(prices) > 30
    for amount in prices:
        for lang in PRICE_LANGUAGES:
            spoken = format_inr_spoken(amount, lang)
            assert spoken and "₹" not in spoken
