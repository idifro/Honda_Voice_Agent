from honda_voice.domain.catalog_view import bike_summary, compare_view, list_item


def test_variant_prices_spoken_in_session_language(catalog):
    shine = catalog.models["shine-100"]
    hi = bike_summary(shine, "hi-IN")
    assert hi["variants"] == [{"variant_name": "Standard", "spoken_price": "lagbhag 65 hazaar 6 sau rupaye"}]
    ta = bike_summary(shine, "ta-IN")
    assert ta["variants"][0]["spoken_price"] == "சுமார் 65,600 ரூபாய்"
    assert hi["price_note"].startswith("ex-showroom Delhi")


def test_unverified_fields_never_returned(catalog):
    shine = bike_summary(catalog.models["shine-100"], "en-IN")
    for field in ("max_power", "max_torque", "fuel_tank_l"):  # listed in unverified_fields
        assert field not in shine["specs"]
    assert shine["mileage_kmpl"] == "77 (Autocar India tested efficiency)"
    hness = bike_summary(catalog.models["cb350-hness"], "en-IN")
    assert "variants" not in hness  # the variant list itself is unverified
    assert "fuel_tank_l" not in hness["specs"]


def test_missing_variant_prices_fall_back_to_range(catalog):
    rebel = bike_summary(catalog.models["rebel-500"], "hi-IN")  # E-Clutch not launched: no price
    assert [v["spoken_price"] is None for v in rebel["variants"]] == [False, True]
    assert "variant_price_instruction" in rebel
    assert rebel["price_min_spoken"] == "5 lakh 12 hazaar rupaye"
    sp125 = bike_summary(catalog.models["sp-125"], "hi-IN")  # one variant unpriced
    assert [v["spoken_price"] is None for v in sp125["variants"]] == [False, False, True]


def test_model_without_price(catalog):
    model = dict(catalog.models["shine-100"], price={"min": None, "max": None}, on_road=[])
    out = bike_summary(model, "en-IN")
    assert out["price_available"] is False and "price_min_spoken" not in out


def test_upcoming_model_cannot_be_booked(catalog):
    model = dict(catalog.models["shine-100"], status="upcoming")
    assert "booking_note" in bike_summary(model, "en-IN")


def test_all_verified_specs_features_and_colours_returned(catalog):
    activa = bike_summary(catalog.models["activa-125"], "en-IN")
    assert activa["specs"]["kerb_weight_kg"] == "107 (H-Smart (official spec page))"  # nested value flattened
    assert activa["specs"]["ground_clearance_mm"] == 162 and activa["specs"]["front_brake"] == "Disc 190 mm"
    assert (
        activa["features"]
        and "mileage_kmpl" not in activa["specs"]
        and "displacement_cc" not in activa["specs"]
    )


def test_on_road_breakdown_in_session_language(catalog):
    shine = bike_summary(catalog.models["shine-100"], "hi-IN")["on_road"]
    assert shine["available"] and shine["city"] == "Delhi"
    assert shine["from_spoken"] == "lagbhag 78 hazaar 7 sau rupaye"  # ₹78,653
    b = shine["breakdown"]
    assert (b["rto_spoken"], b["insurance_spoken"]) == (
        "lagbhag 6 hazaar 3 sau rupaye",
        "lagbhag 5 hazaar 8 sau rupaye",
    )
    assert b["other"] == [
        {"label": "Basic accessories kit", "spoken": "lagbhag 9 sau rupaye", "deduction": False}
    ]
    tamil = bike_summary(catalog.models["activa-110"], "ta-IN")["on_road"]
    assert [v["variant"] for v in tamil["variants"]][0] == "STD"  # cheapest first
    assert tamil["from_spoken"] == "சுமார் 90,700 ரூபாய்"  # ₹90,699 rounds


def test_subsidy_is_a_deduction(catalog):
    qc1 = bike_summary(catalog.models["qc1"], "en-IN")["on_road"]["breakdown"]["other"]
    assert qc1 == [{"label": "PM E-Drive subsidy", "spoken": "5,000 rupees", "deduction": True}]


def test_rows_needing_review_are_never_returned(catalog):
    assert bike_summary(catalog.models["qc3"], "en-IN")["on_road"]["available"] is False
    assert (
        bike_summary(catalog.models["rebel-500"], "en-IN")["on_road"]["available"] is False
    )  # no Delhi price


def test_list_and_compare_views(catalog):
    cheapest = [list_item(m, "en-IN")["name"] for m in catalog.list_models(max_price_inr=80_000)]
    assert cheapest[0] == "Honda Shine 100" and len(cheapest) <= 4
    scooters = catalog.list_models(segment="scooter")
    assert all(m["segment"] == "scooter" for m in scooters)
    a = compare_view(catalog.models["unicorn"], "en-IN")
    assert a["price_min_spoken"] == "about 1 lakh 16 thousand rupees"
    assert "max_power" not in a["specs"]  # unverified for the Unicorn
    assert a["on_road_from_spoken"]
