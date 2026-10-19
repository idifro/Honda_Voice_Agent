import pytest

from honda_voice.domain.competitors import PIVOTS, competitor_pivot


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Royal Enfield Classic 350", (True, "cb350-hness")),
        ("TVS Jupiter", (True, "activa-125")),
        ("Bajaj Pulsar kaisi bike hai?", (True, "sp-160")),
        ("Hero Super Splendor", (True, "shine-125")),  # longest match: not plain "splendor"
        ("hero splendor+", (True, "shine-100")),
        ("Ather Rizta", (True, "qc1")),
        ("Bajaj", (True, None)),  # brand only: no pivot target
        ("Activa 125", (False, None)),
        ("ground clearance", (False, None)),
    ],
)
def test_competitor_pivot(text, expected):
    assert competitor_pivot(text) == expected


def test_pivots_are_rideable_catalog_models(catalog):
    for bike_id in set(PIVOTS.values()):
        assert catalog.models[bike_id]["status"] == "on_sale"
