import pytest

from honda_voice.domain.scope import out_of_scope_topic


@pytest.mark.parametrize(
    ("text", "topic"),
    [
        ("Meri Activa ki servicing kab karwani chahiye?", "servicing or repairs"),
        ("I need to renew my bike insurance", "insurance renewal or claims"),
        ("ನನ್ನ ಬೈಕ್ insurance renewal ಬಗ್ಗೆ ಹೇಳಿ", "insurance renewal or claims"),
        ("Shine 100 EMI kitna hoga?", None),  # in scope since Phase 7 step 3 (emi_examples)
        ("What's the insurance part of the on-road price?", None),  # breakdown question: in scope
        ("Activa 125 ka price kya hai?", None),
        ("Is the showroom open tomorrow?", None),
    ],
)
def test_out_of_scope_topic(text, topic):
    assert out_of_scope_topic(text) == topic


def test_asks_about_price():
    from honda_voice.domain.scope import asks_about_price

    assert asks_about_price("Activa 125 ka price kya hai?") and asks_about_price("Shine 125 விலை என்ன?")
    assert not asks_about_price("Compare the Unicorn and the SP160")
