"""Shape a catalog model into the dict `find_bike` returns to the LLM (SPEC §6, §7).

Applies the data-quality rules: unverified fields are never returned, a missing variant price falls back
to the model's price range, and prices are worded only by `format_inr_spoken` in the session language.
"""

from typing import Any

from honda_voice.domain.pricing import format_inr_spoken

# Specs returned under their own keys rather than in `specs`.
_TOP_LEVEL_SPECS = ("displacement_cc", "mileage_kmpl")


def _plain(value: Any) -> Any:
    """Flatten catalog values for speech: {'value': 102, 'basis': 'claimed'} -> '102 (claimed)';
    {'H-Smart (official spec page)': 107} -> '107 (H-Smart (official spec page))'; lists -> 'a, b'."""
    if isinstance(value, dict):
        if "value" in value:
            basis = value.get("basis")
            return f"{value['value']} ({basis})" if basis else value["value"]
        return "; ".join(f"{v} ({k})" for k, v in value.items()) or None
    if isinstance(value, list):
        return ", ".join(map(str, value)) or None
    return value


def bike_summary(model: dict[str, Any], lang: str) -> dict[str, Any]:
    unverified = set(model.get("unverified_fields") or [])
    specs = model.get("specs") or {}
    price = model.get("price") or {}

    out: dict[str, Any] = {
        "bike_id": model["id"],
        "name": model["name"],
        "segment": model.get("segment"),
        "status": model.get("status"),
    }
    if "displacement_cc" in specs and "displacement_cc" not in unverified:
        out["engine_cc"] = specs["displacement_cc"]
    mileage = specs.get("mileage_kmpl")
    if mileage is not None and "mileage_kmpl" not in unverified:
        out["mileage_kmpl"] = _plain(mileage)
    # Every verified spec, so specific questions can be answered; the prompt says answer only what's asked.
    verified = {k: _plain(v) for k, v in specs.items() if k not in unverified and k not in _TOP_LEVEL_SPECS}
    out["specs"] = {k: v for k, v in verified.items() if v is not None}
    for key in ("features", "colours"):
        if model.get(key) and key not in unverified:
            out[key] = model[key]
    if model.get("pitch"):
        out["pitch"] = model["pitch"]

    lo, hi = price.get("min"), price.get("max")
    if lo is None:
        out["price_available"] = False
        out["price_instruction"] = "No price on record: say the showroom will share it and offer a callback."
    else:
        out["price_available"] = True
        out["price_min_spoken"] = format_inr_spoken(lo, lang)
        if hi and hi != lo:
            out["price_max_spoken"] = format_inr_spoken(hi, lang)
        out["price_note"] = (
            f"ex-showroom {price.get('city', 'Delhi')}, as of {price.get('as_of')}, approximate demo price"
        )

    if "variants" not in unverified:
        variants = []
        for v in model.get("variants") or []:
            inr = v.get("ex_showroom_inr")
            variants.append(
                {"variant_name": v["name"], "spoken_price": format_inr_spoken(inr, lang) if inr else None}
            )
        out["variants"] = variants
        if any(v["spoken_price"] is None for v in variants) and out["price_available"]:
            out["variant_price_instruction"] = (
                "Some variant prices are unknown: quote the model's price range instead and say the exact "
                "variant price will come from the showroom."
            )

    if model.get("status") == "upcoming":
        out["booking_note"] = "Upcoming model: test rides can't be booked yet; offer to take their details."
    if "on_road" in model:
        out["on_road"] = on_road_summary(model["on_road"], lang) or {
            "available": False,
            "instruction": "No published on-road price: depends on city, RTO, insurance; offer a callback.",
        }
    return out


def _spoken(amount: int | None, lang: str) -> str | None:
    if amount is None:
        return None
    try:
        return format_inr_spoken(abs(int(amount)), lang)
    except ValueError:  # amounts under 100 rupees aren't worth speaking
        return None


def on_road_summary(rows: list[dict[str, Any]], lang: str) -> dict[str, Any] | None:
    """Published on-road prices (SPEC §6, §8): the cheapest, its breakdown, and other variants' totals.
    Rows flagged `needs_review` are never returned, so they can't be spoken."""
    usable = sorted((r for r in rows if not r.get("needs_review")), key=lambda r: r["on_road_inr"])
    if not usable:
        return None
    base = usable[0]
    out: dict[str, Any] = {
        "available": True,
        "city": base["city"],
        "from_spoken": _spoken(base["on_road_inr"], lang),
        "cheapest_variant": base["variant_label"],
        "variants": [
            {"variant": r["variant_label"], "on_road_spoken": _spoken(r["on_road_inr"], lang)} for r in usable
        ],
        "note": (
            f"On-road {base['city']} as published by a listing site on {base['as_of']}; it varies by city "
            "and the showroom gives the exact quote. Say 'on-road', not 'ex-showroom'."
        ),
    }
    detailed = next((r for r in usable if r.get("ex_showroom_basis_inr") is not None), None)
    if detailed:
        out["breakdown"] = {
            "variant": detailed["variant_label"],
            "ex_showroom_spoken": _spoken(detailed["ex_showroom_basis_inr"], lang),
            "rto_spoken": _spoken(detailed["rto_inr"], lang),
            "insurance_spoken": _spoken(detailed["insurance_inr"], lang),
            "other": [
                {
                    "label": o["label"],
                    "spoken": _spoken(o["amount_inr"], lang),
                    "deduction": o["amount_inr"] < 0,
                }
                for o in detailed.get("other_charges") or []
            ],
        }
    return out


def list_item(model: dict[str, Any], lang: str) -> dict[str, Any]:
    """One line of `list_bikes` output."""
    p = model["price"]
    item = {
        "bike_id": model["id"], "name": model["name"], "segment": model["segment"], "status": model["status"],
        "ex_showroom_from_spoken": _spoken(p["min"], lang),
    }  # fmt: skip
    if (on_road := on_road_summary(model.get("on_road") or [], lang)) is not None:
        item["on_road_from_spoken"] = on_road["from_spoken"]
    return item


_COMPARE_SPECS = (
    "max_power", "max_torque", "transmission", "braking_system", "abs", "front_brake",
    "fuel_tank_l", "kerb_weight_kg", "top_speed_kmph", "range_km", "seat_height_mm",
)  # fmt: skip


def compare_view(model: dict[str, Any], lang: str) -> dict[str, Any]:
    """Key fields only, for a side-by-side `compare_bikes` answer."""
    full = bike_summary(model, lang)
    keep = (
        "bike_id",
        "name",
        "segment",
        "status",
        "engine_cc",
        "mileage_kmpl",
        "price_min_spoken",
        "price_max_spoken",
    )
    out = {k: full[k] for k in keep if k in full}
    out["specs"] = {k: v for k, v in full.get("specs", {}).items() if k in _COMPARE_SPECS}
    if (on_road := full.get("on_road")) and on_road.get("available"):
        out["on_road_from_spoken"] = on_road["from_spoken"]
    return out
