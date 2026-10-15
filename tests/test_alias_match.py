import pytest

from honda_voice.db.catalog_seed import build_rows
from honda_voice.domain.alias_match import AliasIndex


@pytest.fixture(scope="module")
def index():
    return AliasIndex(build_rows().aliases)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Step 7: the shorter alias inside a longer one must not also match.
        ("What s the price of Activa 125?", ["activa-125"]),
        ("Hi so how much mileage does the Shine 100 give?", ["shine-100"]),
        ("Dio 125 mileage எவ்வளோங்க கொடுக்கும்?", ["dio-125"]),
        ("இந்த Activa electric Activa E இதை பற்றி கொஞ்சம் சொல்லுங்களேன்.", ["activa-e"]),
        # Bare names still resolve to the model that owns them.
        ("tell me about the activa", ["activa-110"]),
        ("shine ka price kya hai", ["shine-125"]),
        # Two models, in order of appearance.
        ("Actually which is better the Unicorn or the Hornet 2.0?", ["unicorn", "hornet-2-0"]),
        ("Shine 100 aur Activa 125 mein kya fark hai", ["shine-100", "activa-125"]),
        # STT spellings added in step 9.
        ("Can I take a test ride of the CB350HINCE coming Saturday?", ["cb350-hness"]),
        ("நாளைக்கு highness test ride பண்ண முடியுமா", ["cb350-hness"]),
        ("what models are in CB350 the Hiness or CB350 RS", ["cb350", "cb350-hness", "cb350rs"]),
        # Nothing Honda here.
        ("Bajaj Pulsar kaisi bike hai?", []),
        ("insurance renewal ಬಗ್ಗೆ ಹೇಳಿ", []),
    ],
)
def test_mentions(index, text, expected):
    assert index.mentions(text) == expected


def test_whole_word_only(index):
    assert index.mentions("dioramas and activated carbon") == []


def test_longest_match_with_synthetic_aliases():
    idx = AliasIndex({"activa": "a110", "activa 125": "a125", "125": "x"})
    assert idx.mentions("activa 125 or activa") == ["a125", "a110"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("activa", ["activa-125", "activa-e"]),
        ("hornet", ["cb1000-hornet-sp", "cb125-hornet", "cb750-hornet"]),
        ("Activa 125", []),
        ("shine 100", ["shine-100-dx"]),
        ("livo", []),
    ],
)
def test_similar(index, name, expected):
    assert index.similar(name) == expected
