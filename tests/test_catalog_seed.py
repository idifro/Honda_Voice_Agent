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


def test_enrichment_prices_variants_and_sets_range(rows):
    """_enrichment.json (BikeDekho New Delhi, 2026-10-20) prices variants; the range follows them."""
    variants = {v["id"]: v for v in rows.variants}
    assert variants["activa-e-standard"]["ex_showroom_inr"] == 120133
    activa = {b["id"]: b for b in rows.bikes}["activa-125"]
    assert (activa["price_min_inr"], activa["price_max_inr"], activa["price_precision"]) == (
        89656,
        96122,
        "exact",
    )
    assert variants["dio-125-dlx"]["notes"] == "BikeDekho lists this variant as STD"
    sp125 = {b["id"]: b for b in rows.bikes}["sp-125"]  # Anniversary Edition unpriced -> approx
    assert sp125["price_precision"] == "approx"
    assert variants["sp-125-25-year-anniversary-edition"]["ex_showroom_inr"] is None


def test_enrichment_fills_gaps_and_flags_estimates(rows):
    bikes = {b["id"]: b for b in rows.bikes}
    assert bikes["livo"]["mileage_kmpl"] == 70 and bikes["livo"]["mileage_basis"] == "BikeDekho overall"
    assert bikes["shine-100"]["mileage_kmpl"] == 77  # an existing figure is never replaced
    assert bikes["cb500"]["mileage_kmpl"] == 28 and "mileage_kmpl" in bikes["cb500"]["unverified_fields"]
    assert bikes["activa-125"]["colours"] and bikes["unicorn"]["features"]
    assert (
        bikes["cb125-hornet"]["colours"][0] == "Pearl Siren Blue with Lemon Ice Yellow"
    )  # generated list kept
    assert bikes["activa-e"]["range_km"] == 102 and bikes["activa-e"]["mileage_kmpl"] is None


def test_fit_rows(rows):
    fit = {f["bike_id"]: f for f in rows.fit}
    assert len(fit) == 33
    assert fit["activa-110"]["ease_score"] == 5 and fit["gold-wing-tour"]["ease_score"] == 1
    assert fit["rebel-300"]["kerb_weight_kg"] == 171 and fit["rebel-300"]["is_estimate"]
    assert (
        "parent" not in fit["activa-125"]["rider_fit"]
    )  # vocabulary, not people: first_time, shorter_riders
    assert not fit["activa-110"]["is_estimate"]  # editorial tags approved by the product owner 2026-10-20


def test_finance_rows(rows):
    assert [f["id"] for f in rows.finance] == ["demo-12", "demo-standard", "demo-low-down"]
    assert all(f["is_demo"] for f in rows.finance)


def test_price_overrides_applied(rows):
    bikes = {b["id"]: b for b in rows.bikes}
    for bike_id, price in (("unicorn", 115832), ("sp-160", 118060), ("nx200", 161296)):
        assert bikes[bike_id]["price_available"] and bikes[bike_id]["price_min_inr"] == price
        assert bikes[bike_id]["price_precision"] == "exact"
        assert isinstance(bikes[bike_id]["price_as_of"], date)  # parsed, not a string
    variants = {v["id"]: v for v in rows.variants}
    assert variants["sp-160-single-disc"]["ex_showroom_inr"] == 118060
    assert variants["sp-160-double-disc"]["ex_showroom_inr"] == 123570  # override extended 2026-10-20
    assert not [line for line in rows.report if line.startswith("no price")]


def test_aliases_normalized_and_include_stt_spellings(rows):
    assert all(a == " ".join(a.lower().split()) for a in rows.aliases)
    assert rows.aliases["hiness"] == "cb350-hness" and rows.aliases["cb350hince"] == "cb350-hness"
    assert rows.aliases["anicon"] == "unicorn"
    assert rows.aliases["activa 125"] == "activa-125" and rows.aliases["honda activa 125"] == "activa-125"


def test_on_road_rows(rows):
    assert len(rows.on_road) == 60
    flagged = {(o["bike_id"], o["variant_label"]) for o in rows.on_road if o["needs_review"]}
    assert flagged == {("qc3", "Standard")}
    for o in rows.on_road:  # every complete row adds up
        if o["rto_inr"] is not None:
            extra = sum(c["amount_inr"] for c in json.loads(o["other_charges"]))
            total = o["ex_showroom_basis_inr"] + o["rto_inr"] + o["insurance_inr"] + extra
            assert total == o["on_road_inr"] or o["needs_review"], o
    shine = next(o for o in rows.on_road if o["bike_id"] == "shine-100")
    assert (
        shine["ex_showroom_basis_inr"],
        shine["rto_inr"],
        shine["insurance_inr"],
        shine["on_road_inr"],
    ) == (65642, 6321, 5795, 78653)
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
