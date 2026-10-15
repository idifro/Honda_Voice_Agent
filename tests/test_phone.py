import pytest

from honda_voice.domain.phone import mask, normalize, readback


@pytest.mark.parametrize(
    "raw",
    [
        "9876543210",
        "98765 43210",
        "98765-43210",
        "+91 98765 43210",
        "+919876543210",
        "91 9876543210",
        "0091 98765 43210",
        "09876543210",
        "(987) 654-3210",
        "  9876543210 ",
    ],
)
def test_normalize_formats(raw):
    assert normalize(raw) == "9876543210"


@pytest.mark.parametrize(
    "raw",
    [
        "९८७६५४३२१०",  # Devanagari digits
        "௯௮௭௬௫௪௩௨௧௦",  # Tamil
        "౯౮౭౬౫౪౩౨౧౦",  # Telugu
        "൯൮൭൬൫൪൩൨൧൦",  # Malayalam
        "೯೮೭೬೫೪೩೨೧೦",  # Kannada
    ],
)
def test_normalize_native_script_digits(raw):
    assert normalize(raw) == "9876543210"


@pytest.mark.parametrize(
    "raw",
    [
        "nine eight seven six five four three two one zero",
        "नौ आठ सात छह पाँच चार तीन दो एक शून्य",  # what saaras:v4 produced on 2026-10-15
        "नौ आठ सात छह पांच चार तीन दो एक शून्य",  # alternate spelling of 5
        "ஒன்பது எட்டு ஏழு ஆறு ஐந்து நான்கு மூன்று இரண்டு ஒன்று பூஜ்ஜியம்",
        "తొమ్మిది ఎనిమిది ఏడు ఆరు ఐదు నాలుగు మూడు రెండు ఒకటి సున్నా",
        "ഒമ്പത് എട്ട് ഏഴ് ആറ് അഞ്ച് നാല് മൂന്ന് രണ്ട് ഒന്ന് പൂജ്യം",
        "ಒಂಬತ್ತು ಎಂಟು ಏಳು ಆರು ಐದು ನಾಲ್ಕು ಮೂರು ಎರಡು ಒಂದು ಸೊನ್ನೆ",
        "98765 four three two one zero",  # mixed digits and words
    ],
)
def test_normalize_number_words(raw):
    assert normalize(raw) == "9876543210"


def test_double_and_triple():
    assert normalize("double nine 8 7 6 5 triple four 3") == "9987654443"
    assert normalize("nine double eight 7 6 5 4 3 2 1") == "9887654321"


@pytest.mark.parametrize(
    "raw",
    [
        "5876543210",  # invalid first digit
        "987654321",  # 9 digits
        "98765432100",  # 11 digits, no leading 0
        "919876543",  # country code + too short
        "ninety eight seven six five four three two one zero",  # compound number word
        "nine eight seven six five four three two one",  # 9 digits in words
        "my number is 9876543210",  # extra words: the LLM should pass only the number
        "double 98 76543210",  # double before a multi-digit group
        "9876543210 double",  # dangling repeat word
        "double triple 9",
        "",
        "   ",
    ],
)
def test_normalize_rejects(raw):
    assert normalize(raw) is None


def test_readback_groups_five_and_five():
    assert readback("9876543210") == "98765 43210"


def test_mask_keeps_last_three():
    assert mask("9876543210") == "xxxxxxx210"


@pytest.mark.parametrize("bad", ["98765 43210", "+919876543210", "12345"])
def test_readback_and_mask_require_normalized(bad):
    with pytest.raises(ValueError):
        readback(bad)
    with pytest.raises(ValueError):
        mask(bad)
