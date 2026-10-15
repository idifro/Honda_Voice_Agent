"""Catalog JSON → table rows (no database)."""

import json
import shutil
from datetime import date

import pytest
from pydantic import ValidationError

from honda_voice.db.catalog_seed import CATALOG_DIR, build_rows, load_models


@pytest.fixture(scope="module")
def rows():
    return build_rows()


def test_all_33_models_validate(rows):
    assert len(load_models()) == len(rows.bikes) == 33
    assert len({v["id"] for v in rows.variants}) == len(rows.variants)  # variant ids unique


def test_engine_cc_and_range_by_fuel(rows):
    bikes = {b["id"]: b for b in rows.bikes}
    for b in bikes.values():
        if b["fuel"] == "petrol":
            assert b["engine_cc"] and b["range_km"] is None
    assert bikes["activa-e"]["engine_cc"] is None and bikes["activa-e"]["range_km"] == 102
    assert bikes["qc3"]["range_km"] == 151
    assert bikes["shine-100"]["mileage_kmpl"] == 77
    assert bikes["shine-100"]["mileage_basis"] == "Autocar India tested efficiency"


def test_seventeen_segments_kept(rows):
    assert len({b["segment"] for b in rows.bikes}) == 17


def test_activa_e_price_fixed(rows):
    variants = {v["id"]: v for v in rows.variants}
    assert variants["activa-e-standard"]["ex_showroom_inr"] == 118000
    assert {b["id"]: b for b in rows.bikes}["activa-e"]["price_min_inr"] == 118000


def test_price_overrides_applied(rows):
    bikes = {b["id"]: b for b in rows.bikes}
    for bike_id, price in (("unicorn", 115832), ("sp-160", 118060), ("nx200", 161296)):
        assert bikes[bike_id]["price_available"] and bikes[bike_id]["price_min_inr"] == price
        assert bikes[bike_id]["price_precision"] == "exact"
        assert isinstance(bikes[bike_id]["price_as_of"], date)  # parsed, not a string
    variants = {v["id"]: v for v in rows.variants}
    assert variants["sp-160-single-disc"]["ex_showroom_inr"] == 118060
    assert variants["sp-160-double-disc"]["ex_showroom_inr"] is None
    assert not [line for line in rows.report if line.startswith("no price")]


def test_aliases_normalized_and_include_stt_spellings(rows):
    assert all(a == " ".join(a.lower().split()) for a in rows.aliases)
    assert rows.aliases["hiness"] == "cb350-hness" and rows.aliases["cb350hince"] == "cb350-hness"
    assert rows.aliases["anicon"] == "unicorn"
    assert rows.aliases["activa 125"] == "activa-125" and rows.aliases["honda activa 125"] == "activa-125"


def test_on_road_rows(rows):
    assert len(rows.on_road) == 54
    flagged = {(o["bike_id"], o["variant_label"]) for o in rows.on_road if o["needs_review"]}
    assert ("qc3", "Standard") in flagged and any(b == "cb350-hness" for b, _ in flagged)
    shine = next(o for o in rows.on_road if o["bike_id"] == "shine-100")
    assert (
        shine["ex_showroom_basis_inr"],
        shine["rto_inr"],
        shine["insurance_inr"],
        shine["on_road_inr"],
    ) == (65642, 5781, 6522, 77945)
    assert json.loads(next(o for o in rows.on_road if o["bike_id"] == "qc1")["other_charges"]) == [
        {"label": "PM E-Drive subsidy", "amount_inr": -5000}
    ]


@pytest.fixture
def catalog_copy(tmp_path):
    dst = tmp_path / "catalog"
    shutil.copytree(CATALOG_DIR, dst)
    return dst


def test_alias_collision_is_an_error(catalog_copy):
    path = catalog_copy / "livo.json"
    data = json.loads(path.read_text())
    data["aliases"].append("Shine 100")  # already Shine 100's alias
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="claimed by both"):
        build_rows(catalog_copy)


def test_invalid_segment_rejected(catalog_copy):
    path = catalog_copy / "livo.json"
    data = json.loads(path.read_text())
    data["segment"] = "premium"
    path.write_text(json.dumps(data))
    with pytest.raises(ValidationError):
        build_rows(catalog_copy)


def test_petrol_model_needs_displacement(catalog_copy):
    path = catalog_copy / "livo.json"
    data = json.loads(path.read_text())
    del data["specs"]["displacement_cc"]
    path.write_text(json.dumps(data))
    with pytest.raises(ValidationError, match="displacement_cc"):
        build_rows(catalog_copy)


def test_override_for_unknown_variant_rejected(catalog_copy):
    path = catalog_copy / "_price_overrides.json"
    data = json.loads(path.read_text())
    data["overrides"][0]["variants"] = {"Turbo": 1}
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="unknown variant"):
        build_rows(catalog_copy)
