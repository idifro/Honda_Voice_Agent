"""Builds data/catalog/<id>.json — one file per Honda India model (snapshot Oct 2026).

Conventions
- price: ex-showroom Delhi unless price.city says otherwise. min/max in INR.
  precision "approx" = listing sites round (e.g. "₹72.15 thousand").
- variants[].ex_showroom_inr is null where the per-variant price wasn't confirmed.
  "inferred": true means mapped from a min/max range (lowest price -> base variant).
- unverified_fields: spec keys filled from widely published manufacturer figures but NOT
  re-confirmed from a source fetched for this snapshot. Check them against the brochure.
- spoken_price is intentionally absent: scripts/seed_catalog.py generates it.
"""
import json, os
from datetime import date

OUT = "data/catalog"
SNAP = "2026-10-14"
AC = "https://www.autocarindia.com/bikes/honda-bikes"
AC1L = "https://www.autocarindia.com/bikes/honda-bikes-under-1-lakh"
H = "https://www.honda2wheelersindia.com"

def price(mn, mx=None, as_of="2026-10-09", src=AC1L, precision="approx", city="Delhi", notes=None):
    p = {"currency": "INR", "type": "ex-showroom", "city": city, "min": mn, "max": mx if mx else mn,
         "as_of": as_of, "precision": precision, "source": src}
    if notes: p["notes"] = notes
    return p

def v(name, inr=None, inferred=False, notes=None):
    d = {"name": name, "ex_showroom_inr": inr}
    if inferred: d["inferred"] = True
    if notes: d["notes"] = notes
    return d

SCOOTER_COMMON_WARRANTY = {"standard_warranty": "3 years / 36,000 km (whichever earlier)"}

M = []

# ---------------- SCOOTERS (petrol) ----------------
M.append(dict(
    id="activa-110", name="Honda Activa 110", segment="scooter", division="RedWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/scooter/activa110",
    price=price(75960, 90830, notes="4 variants per Autocar India listing"),
    variants=[v("STD", 75960, inferred=True), v("DLX"), v("H-Smart"),
              v("25-year Anniversary Edition", notes="Launch price Rs 84,939 ex-showroom Delhi (Aug 2025, pre-GST cut); DLX-based, Pearl Siren Blue / Mat Steel Black Metallic")],
    specs=dict(engine="4-stroke, SI, single-cylinder, air-cooled, PGM-Fi, OBD2B", displacement_cc=109.51,
               max_power="7.88 bhp @ 8000 rpm", max_torque="9.05 Nm @ 5500 rpm", bore_x_stroke_mm="47.0 x 63.121",
               compression_ratio="10.0:1", transmission="Automatic (V-Matic CVT)", starting="STD & DLX: kick/self; H-Smart: self",
               fuel_tank_l=5.3, reserve_fuel_l=1.3, kerb_weight_kg={"STD": 106, "DLX": 106, "H-Smart": 105},
               length_mm=1833, width_mm=677, height_mm=1165, wheelbase_mm=1260, ground_clearance_mm=162,
               seat_length_mm=692, seat_height_mm=764, frame="Underbone", front_suspension="Telescopic",
               rear_suspension="3-step adjustable (unit swing)", front_brake="Drum", rear_brake="Drum", braking_system="CBS",
               tyre_front="90/90-12 54J tubeless", tyre_rear="90/100-10 53J tubeless", wheels="Alloy",
               battery="12V 5.0Ah MF", headlamp="STD: halogen; DLX & H-Smart: LED", top_speed_kmph=85,
               mileage_kmpl={"value": 59.5, "basis": "ARAI (as reported by Autocar India)"},
               engine_oil="10W30"),
    features=["Smart Key with H-Smart (H-Smart variant)", "TFT display", "Honda RoadSync connectivity",
              "Open glove box with 15W USB-C", "Double-lid external fuel fill", "Idling stop system",
              "Side-stand engine cut-off", "LED headlamp with LED position lamp", "Alloy wheels", "Tubeless tyres"],
    service=dict(free_services=["750-1000 km / 15-30 days", "5500-6000 km / 165-180 days", "11500-12000 km / 350-365 days"],
                 paid_service_interval="Every 6,000 km or 6 months", **SCOOTER_COMMON_WARRANTY),
    aliases=["activa", "activa 110", "activa 6g", "aktiva", "ektiva", "activa smart", "activa h smart", "ऐक्टिवा", "एक्टिवा"],
    pitch="India's best-selling scooter: 110cc, about 106 kg, 5.3 L tank, with a Smart Key and TFT display on the H-Smart variant.",
    sources=[f"{H}/scooter/activa110", "https://honda2wheelersindia.com/activa110-OBD2B/specifications",
             "https://www.autocarindia.com/bikes/honda-bikes/activa-6g/specifications", AC1L],
    unverified_fields=[]))

M.append(dict(
    id="activa-125", name="Honda Activa 125", segment="scooter", division="RedWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/scooter/activa125",
    price=price(90800, 95250, notes="3 variants per Autocar India listing"),
    variants=[v("DLX"), v("H-Smart"), v("25-year Anniversary Edition", notes="Launch price Rs 89,256 ex-showroom Delhi (Aug 2025, pre-GST cut)")],
    specs=dict(engine="4-stroke, SI, single-cylinder, air-cooled, PGM-Fi, OBD2B", displacement_cc=123.92,
               max_power="8.3 bhp @ 6500 rpm", max_torque="10.5 Nm @ 5000 rpm", bore_x_stroke_mm="50.0 x 63.1",
               compression_ratio="10.0:1", transmission="Automatic CVT (centrifugal dry clutch)", starting="Self/Kick",
               fuel_tank_l=5.3, kerb_weight_kg={"H-Smart (official spec page)": 107}, length_mm=1850, width_mm=707,
               height_mm=1160, wheelbase_mm=1260, ground_clearance_mm=162, seat_length_mm=712, frame="Underbone",
               front_suspension="Telescopic", rear_suspension="3-step adjustable spring-loaded hydraulic",
               front_brake="Disc 190 mm", rear_brake="Drum 130 mm", braking_system="CBS",
               tyre_front="90/90-12 54J tubeless", tyre_rear="90/100-10 53J tubeless", wheels="Alloy",
               battery="12V 5.0Ah", headlamp="LED", top_speed_kmph=90),
    features=["H-Smart key (H-Smart variant)", "TFT display", "Honda RoadSync", "USB-C charging", "LED headlamp",
              "Idling stop system", "Side-stand engine cut-off"],
    service=SCOOTER_COMMON_WARRANTY,
    aliases=["activa 125", "activa sava sau", "activa ek sau pachees", "ऐक्टिवा 125"],
    pitch="The 125cc Activa: more torque than the 110, front disc brake and LED headlamp, family-friendly scooter.",
    sources=["https://honda2wheelersindia.com/activa125-OBD2B/specifications",
             "https://www.autocarindia.com/bikes/honda-bikes/activa-125/specifications", AC1L],
    unverified_fields=[]))

M.append(dict(
    id="dio-110", name="Honda Dio 110", segment="scooter", division="RedWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/scooter/dio-110",
    price=price(70310, 82360, notes="2 variants per Autocar India listing; Honda's most affordable petrol scooter"),
    variants=[v("STD", 70310, inferred=True), v("DLX", 82360, inferred=True)],
    specs=dict(engine="Single-cylinder, air-cooled, PGM-Fi", displacement_cc=109.51, max_power="7.75 bhp @ 8000 rpm",
               transmission="Automatic CVT", fuel_tank_l=5.3, kerb_weight_kg=103, ground_clearance_mm=160,
               front_brake="Drum", rear_brake="Drum", braking_system="CBS", tyre_front="90/90-12 54J tubeless",
               tyre_rear="90/100-10 53J tubeless", wheels="Alloy", headlamp="LED", top_speed_kmph=83),
    features=["Sporty styling", "Digital instrument cluster", "LED headlamp", "Side-stand engine cut-off", "Tubeless tyres"],
    service=SCOOTER_COMMON_WARRANTY,
    aliases=["dio", "dio 110", "deo", "डियो"],
    pitch="Sporty-styled 110cc scooter and Honda's most affordable petrol scooter.",
    sources=["https://www.smartprix.com/scooters/honda-dio-110-ppd1f3rii19a", AC1L],
    unverified_fields=["max_power", "ground_clearance_mm"]))

M.append(dict(
    id="dio-125", name="Honda Dio 125", segment="scooter", division="RedWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/scooter/dio-125",
    price=price(87260, 92210, notes="3 variants per Autocar India listing"),
    variants=[v("DLX"), v("H-Smart"), v("X-Edition")],
    specs=dict(engine="Single-cylinder, air-cooled, PGM-Fi, OBD2B", displacement_cc=123.92,
               max_power="8.19 bhp @ 6500 rpm", max_torque="10.5 Nm @ 5000 rpm", bore_x_stroke_mm="50 x 63.1",
               transmission="Automatic CVT (centrifugal dry clutch)", fuel_tank_l=5.3, reserve_fuel_l=1.3,
               kerb_weight_kg=105, seat_height_mm=765, ground_clearance_mm=171, length_mm=1830,
               front_suspension="Telescopic", rear_suspension="3-step adjustable spring-loaded hydraulic",
               front_brake="Disc 190 mm", rear_brake="Drum 130 mm", braking_system="CBS",
               tyre_front="90/90-12 tubeless", tyre_rear="90/100-10 tubeless", wheels="Alloy", top_speed_kmph=85,
               mileage_kmpl={"value": 48, "basis": "ARAI (as reported by Team-BHP)"}),
    features=["Smart key (H-Smart)", "Idling stop", "Distance-to-empty indicator", "LED headlamp"],
    service=SCOOTER_COMMON_WARRANTY,
    aliases=["dio 125", "dio sava sau", "dio x edition"],
    pitch="Sportier 125cc sibling of the Dio with front disc and smart-key option.",
    sources=["https://www.autocarindia.com/bikes/honda-bikes/dio-125/specifications",
             "https://www.team-bhp.com/new-bikes/honda/dio-125/specifications/", AC1L],
    unverified_fields=[]))

M.append(dict(
    id="adv160", name="Honda ADV160", segment="adventure-scooter", division="RedWing/BigWing", fuel="petrol", status="pre_booking",
    official_url=f"{H}/scooter/adv160-new",
    price=price(170000, as_of="2026-10-12", src=AC, notes="Pre-book listing"),
    variants=[v("Standard", 170000)],
    specs=dict(engine="160cc single-cylinder, water-cooled", displacement_cc=157, fuel_tank_l=8.1,
               underseat_storage_l=27, fuel_compatibility="Up to E85 (first scooter in India per Honda)",
               transmission="Automatic CVT"),
    features=["City-adventure styling", "E85 flex-fuel capable", "8.1 L tank", "27 L under-seat storage"],
    aliases=["adv", "adv 160", "adv160", "a d v"],
    pitch="City-adventure scooter: water-cooled 160cc, 8.1 L tank, 27 L storage, runs on up to E85 fuel.",
    sources=["https://www.honda2wheelersindia.com/news/honda-motorcycle-scooter-india-unveils-10-product-made-in-india", AC],
    unverified_fields=[]))

# ---------------- EV ----------------
M.append(dict(
    id="activa-e", name="Honda Activa e:", segment="electric-scooter", division="EV", fuel="electric", status="on_sale",
    official_url=f"{H}/e2w/products/activa-e",
    price=price(118000, 151600, as_of="2026-10-14", src="https://www.zigwheels.com/bike-comparison/honda-activa-electric-vs-honda-qc1",
                notes="Launch prices: Standard Rs 1,17,000, RoadSync Duo Rs 1,51,600. A 2026 report says production was paused; confirm availability with dealer."),
    variants=[v("Standard", 117000, notes="launch price"), v("RoadSync Duo", 151600, notes="launch price")],
    specs=dict(battery="2 x 1.5 kWh Honda Mobile Power Pack e: (swappable)", range_km={"value": 102, "basis": "claimed"},
               ride_modes=["Econ", "Standard", "Sport"], charging="Battery swapping via Honda e:Swap stations",
               display="7-inch TFT with RoadSync Duo (top variant)"),
    features=["Swappable batteries", "Reverse mode", "Smart key", "Topple alert, navigation, OTA (RoadSync Duo)"],
    aliases=["activa e", "activa electric", "electric activa", "activa ev", "बिजली वाली ऐक्टिवा"],
    pitch="Electric Activa with two swappable batteries and about 102 km claimed range; relies on Honda swap stations.",
    notes=["Limited under-seat storage because of the dual-battery layout.", "Sold in select cities with e:Swap network."],
    sources=["https://gaadify.com/honda-activa-e-and-honda-qc1-electric-scooters-launched/?amp=1",
             "https://www.motorbeam.com/?p=315007", "https://www.91wheels.com/scooter/honda/qc"],
    unverified_fields=[]))

M.append(dict(
    id="qc1", name="Honda QC1", segment="electric-scooter", division="EV", fuel="electric", status="on_sale",
    official_url=f"{H}/e2w/products/qc1",
    price=price(90490),
    variants=[v("Standard", 90490)],
    specs=dict(battery="1.5 kWh fixed", range_km={"value": 80, "basis": "claimed"}, top_speed_kmph=50,
               charging="350W on-board charger, ~6.5 h full charge (claimed)", underseat_storage_l=26),
    features=["Home charging", "26 L under-seat storage"],
    aliases=["qc1", "q c one", "qc 1", "quick commuter"],
    pitch="Low-speed city EV with a fixed 1.5 kWh battery, about 80 km range and home charging.",
    sources=[AC1L, "https://www.91wheels.com/scooter/honda/qc"],
    unverified_fields=[]))

M.append(dict(
    id="qc3", name="Honda QC3", segment="electric-scooter", division="EV", fuel="electric", status="on_sale",
    official_url=f"{H}/e2w/products/qc3",
    price=price(135000, as_of="2026-10-12", src=AC),
    variants=[v("Standard", 135000)],
    specs=dict(battery="3 kWh", range_km={"value": 151, "basis": "IDC (Honda)", "alt": "145 km as listed by Autocar India"},
               charging="0-80% in 2 h 40 min (claimed)", underseat_storage_l=32, display="5-inch TFT, connected features"),
    features=["Smart key", "32 L under-seat storage", "Connected TFT"],
    aliases=["qc3", "q c three", "qc 3"],
    pitch="Family EV scooter: 3 kWh battery, 151 km IDC range, 32 L storage and smart key.",
    sources=["https://www.honda2wheelersindia.com/news/honda-motorcycle-scooter-india-unveils-10-product-made-in-india", AC],
    unverified_fields=[]))

# ---------------- REDWING MOTORCYCLES ----------------
M.append(dict(
    id="shine-100", name="Honda Shine 100", segment="commuter", division="RedWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/motorcycle/shine-100",
    price=price(65640, notes="Honda's cheapest motorcycle"),
    variants=[v("Standard", 65640)],
    specs=dict(engine="Single-cylinder, air-cooled, fuel-injected, OBD2B", displacement_cc=98.98,
               max_power="5.43 kW @ 7500 rpm", max_torque="8.05 Nm @ 5000 rpm", transmission="4-speed manual",
               fuel_tank_l=9, front_brake="Drum", rear_brake="Drum", braking_system="CBS", top_speed_kmph=85,
               mileage_kmpl={"value": 77, "basis": "Autocar India tested efficiency"}),
    aliases=["shine 100", "shine sau", "shine hundred", "शाइन 100"],
    pitch="Honda's most affordable motorcycle: 99cc, light, simple and very fuel-efficient.",
    sources=[AC1L, AC],
    unverified_fields=["max_power", "max_torque", "fuel_tank_l"]))

M.append(dict(
    id="shine-100-dx", name="Honda Shine 100 DX", segment="commuter", division="RedWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/motorcycle/shine-100-dx",
    price=price(72150, notes="Launched Aug 2025 at Rs 74,959 ex-showroom Gurugram (pre-GST cut)"),
    variants=[v("DX", 72150)],
    specs=dict(engine="Single-cylinder, fuel-injected, eSP, OBD2B", displacement_cc=98.98,
               max_power="5.43 kW @ 7500 rpm", max_torque="8.04 Nm @ 5000 rpm", transmission="4-speed manual",
               front_suspension="Telescopic", rear_suspension="5-step adjustable", front_brake="Drum", rear_brake="Drum",
               braking_system="CBS", instrument_cluster="Digital LCD (real-time mileage, distance-to-empty, service reminder)",
               top_speed_kmph=85),
    colours=["Pearl Igneous Black", "Imperial Red Metallic", "Athletic Blue Metallic", "Geny Gray Metallic"],
    features=["Chrome-garnished headlamp", "Long seat", "Chrome muffler cover", "High ground clearance"],
    aliases=["shine 100 dx", "shine dx", "shine 100 deluxe"],
    pitch="Premium-looking Shine 100 with a digital LCD showing real-time mileage and distance-to-empty.",
    sources=["https://www.honda2wheelersindia.com/news/honda-motorcycle-scooter-india-announces-prices-for-the-all-new-shine-100-dx", AC1L],
    unverified_fields=[]))

M.append(dict(
    id="livo", name="Honda Livo", segment="commuter", division="RedWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/motorcycle/livo",
    price=price(80080, 82650, notes="2 variants per Autocar India"),
    variants=[v("Drum", 80080, inferred=True), v("Disc", 82650, inferred=True)],
    specs=dict(engine="Single-cylinder, air-cooled, PGM-Fi", displacement_cc=109.51, max_power="about 8.55 hp",
               max_torque="9.30 Nm @ 5500 rpm", transmission="4-speed manual", seat_height_mm=790, fuel_tank_l=9,
               top_speed_kmph=85, mileage_kmpl={"value": "58-65", "basis": "Autocar India efficiency"}),
    aliases=["livo", "leevo", "लिवो"],
    pitch="110cc commuter motorcycle with a manual 4-speed gearbox and 58-65 kmpl real-world efficiency.",
    sources=[AC1L, AC],
    unverified_fields=["max_torque", "fuel_tank_l"]))

M.append(dict(
    id="shine-125", name="Honda Shine 125", segment="commuter", division="RedWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/motorcycle/shine-125",
    price=price(82250, 87610, notes="3 variants: Drum, Disc, Limited Edition"),
    variants=[v("Drum", 82250, inferred=True), v("Disc"), v("Limited Edition")],
    specs=dict(engine="Single-cylinder, air-cooled, PGM-Fi, OBD2B", displacement_cc=123.94,
               max_power="7.93 kW @ 7500 rpm", max_torque="11 Nm @ 6000 rpm", transmission="5-speed manual",
               fuel_tank_l=10.5, top_speed_kmph=102, mileage_kmpl={"value": "50-55", "basis": "Autocar India efficiency"}),
    aliases=["shine", "shine 125", "shine sava sau", "शाइन"],
    pitch="India's top-selling 125cc motorcycle: refined, comfortable and frugal.",
    sources=[AC1L, AC, "https://bikeadvice.in/?p=92857"],
    unverified_fields=["max_power", "max_torque", "fuel_tank_l"]))

M.append(dict(
    id="sp-125", name="Honda SP125", segment="commuter", division="RedWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/motorcycle/sp-125",
    price=price(89280, 96870, notes="2 variants per Autocar India; a 25-year Anniversary Edition launched Aug 2025 at Rs 94,069 (pre-GST cut)"),
    variants=[v("Drum", 89280, inferred=True), v("Disc", 96870, inferred=True),
              v("25-year Anniversary Edition", notes="Availability may vary")],
    specs=dict(engine="Single-cylinder, air-cooled, PGM-Fi, OBD2B", displacement_cc=123.94,
               max_power="8 kW @ 7500 rpm", max_torque="10.9 Nm @ 6000 rpm", transmission="5-speed manual",
               fuel_tank_l=11.2, headlamp="Full LED", instrument_cluster="4.2-inch TFT", charging="USB Type-C",
               top_speed_kmph=100, mileage_kmpl={"value": 61.87, "basis": "Autocar India efficiency"}),
    features=["4.2-inch TFT", "LED headlamp", "USB-C charging", "Side-stand engine inhibitor"],
    aliases=["sp 125", "sp125", "s p 125", "sp sava sau", "एसपी 125"],
    pitch="Sporty 125cc commuter with LED headlamp and 4.2-inch TFT, about 62 kmpl.",
    sources=[AC1L, AC, "https://www.honda2wheelersindia.com/news/honda-motorcycle-scooter-india-celebrates-25-years-of-trust-launches-25-year-anniversary-editions"],
    unverified_fields=["max_power", "max_torque", "fuel_tank_l"]))

M.append(dict(
    id="cb125-hornet", name="Honda CB125 Hornet", segment="sporty-commuter", division="RedWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/motorcycle/CB125-Hornet",
    price=price(115000, as_of="2026-10-12", src=AC, notes="Launched Aug 2025 at introductory Rs 1,12,000 ex-showroom Gurugram"),
    variants=[v("Standard", 115000)],
    specs=dict(engine="123.94cc single-cylinder, fuel-injected, OBD2B", displacement_cc=123.94,
               max_power="8.2 kW @ 7500 rpm", max_torque="11.2 Nm @ 6000 rpm", transmission="5-speed manual",
               front_suspension="Golden USD fork (37 mm, first in segment)", rear_suspension="5-step adjustable monoshock",
               front_brake="240 mm petal disc", abs="Single-channel", instrument_cluster="4.2-inch TFT with Honda RoadSync",
               headlamp="Twin LED with DRLs", charging="USB Type-C", acceleration="0-60 km/h in 5.4 s (claimed)"),
    colours=["Pearl Siren Blue with Lemon Ice Yellow", "Pearl Igneous Black", "Pearl Siren Blue with Athletic Blue Metallic",
             "Pearl Siren Blue with Sports Red"],
    aliases=["cb125 hornet", "hornet 125", "cb 125", "chhota hornet"],
    pitch="Sportiest Honda 125: golden USD fork, single-channel ABS, TFT with RoadSync, 0-60 in 5.4 s.",
    sources=["https://www.honda2wheelersindia.com/news/honda-motorcycle-scooter-india-announces-prices-for-the-all-new-cb125-hornet", AC],
    unverified_fields=[]))

M.append(dict(
    id="unicorn", name="Honda Unicorn", segment="premium-commuter", division="RedWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/motorcycle/unicorn",
    price=dict(currency="INR", type="ex-showroom", city="Delhi", min=None, max=None, as_of=None, precision="missing",
               source=None, notes="Current price not confirmed in this snapshot; fill before seeding"),
    variants=[v("Standard")],
    specs=dict(engine="Single-cylinder, air-cooled, PGM-Fi", displacement_cc=162.71, max_power="9.7 kW @ 7500 rpm",
               max_torque="14 Nm @ 5500 rpm", transmission="5-speed manual", fuel_tank_l=13, abs="Single-channel",
               mileage_kmpl={"value": "50-55", "basis": "listing sites"}),
    aliases=["unicorn", "unikorn", "यूनिकॉर्न"],
    pitch="Comfort-first 160cc commuter with a plush ride, known for durability.",
    sources=["https://www.bajajfinserv.in/telugu/bmall/bikes/200-cc"],
    unverified_fields=["max_power", "max_torque", "abs"]))

M.append(dict(
    id="sp-160", name="Honda SP160", segment="premium-commuter", division="RedWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/motorcycle/sp-160",
    price=dict(currency="INR", type="ex-showroom", city="Delhi", min=None, max=None, as_of=None, precision="missing",
               source=None, notes="Fill before seeding"),
    variants=[v("Single Disc"), v("Double Disc")],
    specs=dict(engine="Single-cylinder, air-cooled, PGM-Fi", displacement_cc=162.71, max_power="9.9 kW @ 7500 rpm",
               max_torque="14.58 Nm @ 5500 rpm", transmission="5-speed manual", fuel_tank_l=12),
    aliases=["sp 160", "sp160", "s p 160"],
    pitch="Sporty-styled 160cc commuter, step up from the SP125.",
    sources=["https://www.bajajfinserv.in/telugu/bmall/bikes/200-cc"],
    unverified_fields=["max_power", "max_torque", "fuel_tank_l"]))

M.append(dict(
    id="hornet-2-0", name="Honda Hornet 2.0", segment="street", division="RedWing/BigWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/motorcycle/hornet-2-0",
    price=price(148000, as_of="2026-07-04", src="https://www.autocarindia.com/bikes/honda-bikes/hornet-2.0/price-in-delhi"),
    variants=[v("Standard", 148000)],
    specs=dict(engine="Single-cylinder, air-cooled, PGM-Fi", displacement_cc=184.40, max_power="12.7 kW @ 8500 rpm",
               max_torque="15.9 Nm @ 6000 rpm", transmission="5-speed manual, assist & slipper clutch",
               front_suspension="USD fork", abs="Single-channel", fuel_tank_l=12),
    aliases=["hornet", "hornet 2.0", "hornet two point o", "hornet 200", "हॉर्नेट"],
    pitch="184cc street-naked with USD fork and slipper clutch; the sporty pick under 1.5 lakh.",
    sources=["https://www.autocarindia.com/bikes/honda-bikes/hornet-2.0/price-in-delhi"],
    unverified_fields=["max_power", "max_torque", "transmission", "abs", "fuel_tank_l"]))

M.append(dict(
    id="nx200", name="Honda NX200", segment="adventure-crossover", division="RedWing/BigWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/motorcycle/nx200",
    price=dict(currency="INR", type="ex-showroom", city="Delhi", min=None, max=None, as_of=None, precision="missing",
               source=None, notes="Fill before seeding"),
    variants=[v("Standard")],
    specs=dict(engine="Single-cylinder, air-cooled, PGM-Fi", displacement_cc=184.40, transmission="5-speed manual",
               front_suspension="USD fork"),
    aliases=["nx200", "nx 200", "n x 200"],
    pitch="Hornet 2.0-based urban adventure crossover with upright ergonomics.",
    sources=[f"{H}/"],
    unverified_fields=["front_suspension"]))

# ---------------- BIGWING ----------------
CB350_ENGINE = dict(engine="348.36cc air-cooled 4-stroke single, PGM-FI, OBD2B, E20 compliant", displacement_cc=348.36,
                    max_power="15.5 kW @ 5500 rpm", max_torque="29.5 Nm @ 3000 rpm", transmission="5-speed manual, assist & slipper clutch",
                    traction_control="HSTC (Honda Selectable Torque Control)", abs="Dual-channel",
                    connectivity="Honda Smartphone Voice Control (HSVCS)")
M.append(dict(
    id="cb350-hness", name="Honda CB350 H'ness", segment="retro-classic", division="BigWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/motorcycle/cb350-hness",
    price=price(192000, 197000, as_of="2026-06-30", src="https://www.autocarindia.com/bikes/honda-bikes/cb300r",
                notes="June 2026 Autocar India price list"),
    variants=[v("DLX"), v("DLX Pro")],
    specs=dict(**CB350_ENGINE, fuel_tank_l=15),
    aliases=["hness", "highness", "h'ness", "cb350 hness", "हाइनेस"],
    pitch="Honda's retro-classic 350: smooth, light for its class, with dual-channel ABS and HSTC.",
    sources=["https://www.autocarindia.com/bikes/honda-bikes/cb300r",
             "https://www.honda2wheelersindia.com/news/honda-motorcycle-and-scooter-india-launches-all-new-cb350c-special-edition"],
    unverified_fields=["fuel_tank_l", "variants"]))

M.append(dict(
    id="cb350rs", name="Honda CB350RS", segment="retro-scrambler", division="BigWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/motorcycle/CB350RS",
    price=price(197000, 200000, as_of="2026-06-30", src="https://www.autocarindia.com/bikes/honda-bikes/cb300r"),
    variants=[v("DLX"), v("DLX Pro")],
    specs=dict(**CB350_ENGINE, seat_height_mm=800, fuel_tank_l=15),
    notes=["New colours announced July 2026 ('CB350RS New' on Honda site)."],
    aliases=["cb350rs", "cb 350 rs", "rs 350", "350 rs"],
    pitch="Sportier, scrambler-styled take on the CB350 platform.",
    sources=["https://www.autocarindia.com/bikes/honda-bikes/cb300r", "https://www.zigwheels.com/newbikes/Honda/2022-cb1000r"],
    unverified_fields=["fuel_tank_l", "variants"]))

M.append(dict(
    id="cb350", name="Honda CB350", segment="retro-classic", division="BigWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/motorcycle/CB350",
    price=price(197000, 200000, as_of="2026-06-30", src="https://www.autocarindia.com/bikes/honda-bikes/cb300r"),
    variants=[v("DLX"), v("DLX Pro")],
    specs=dict(**CB350_ENGINE),
    notes=["New colours announced July 2026 ('CB350 New')."],
    aliases=["cb350", "cb 350", "c b 350"],
    pitch="Classic-styled CB350 with the same refined 348cc engine.",
    sources=["https://www.autocarindia.com/bikes/honda-bikes/cb300r"],
    unverified_fields=["variants"]))

M.append(dict(
    id="cb350c", name="Honda CB350C", segment="retro-classic", division="BigWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/motorcycle/CB350C",
    price=price(200000, 205000, as_of="2026-10-12", src=AC, notes="3 variants incl. Special Edition"),
    variants=[v("DLX"), v("DLX Pro"),
              v("Special Edition", notes="Launched Sep 2025 at Rs 2,01,900 ex-showroom Bengaluru; Rebel Red Metallic, Matt Dune Brown")],
    specs=dict(**CB350_ENGINE, top_speed_kmph=130, instrument_cluster="Digital-analogue"),
    aliases=["cb350c", "cb 350 c", "350 c"],
    pitch="Rebranded classic CB350 with Special Edition stripes, dual-channel ABS and HSTC.",
    sources=["https://www.honda2wheelersindia.com/news/honda-motorcycle-and-scooter-india-launches-all-new-cb350c-special-edition", AC],
    unverified_fields=["variants"]))

M.append(dict(
    id="rebel-300", name="Honda Rebel 300", segment="cruiser", division="BigWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/motorcycle/Rebel300",
    price=price(222000, 262000, as_of="2026-10-12", src=AC),
    variants=[v("Standard", 222000, inferred=True), v("E-Clutch", 262000, inferred=True)],
    specs=dict(engine="286cc liquid-cooled single", displacement_cc=286, transmission="6-speed manual; E-Clutch variant automates clutch"),
    aliases=["rebel", "rebel 300", "rebal"],
    pitch="Entry bobber-style cruiser with low seat; E-Clutch option for easy city riding.",
    sources=["https://www.honda2wheelersindia.com/news/honda-motorcycle-scooter-india-unveils-10-product-made-in-india", AC],
    unverified_fields=["transmission"]))

M.append(dict(
    id="xr300l", name="Honda XR300L", segment="dual-sport", division="BigWing", fuel="petrol", status="pre_booking",
    official_url=f"{H}/motorcycle/xr300l",
    price=price(183000, as_of="2026-10-12", src=AC),
    variants=[v("Standard", 183000)],
    specs=dict(engine="293.5cc air-cooled single with oil-cooler", displacement_cc=293.5, suspension="Long-travel"),
    aliases=["xr300l", "xr 300", "x r 300 l"],
    pitch="Light dual-purpose bike for weekend trails and weekday city use.",
    sources=["https://www.honda2wheelersindia.com/news/honda-motorcycle-scooter-india-unveils-10-product-made-in-india", AC],
    unverified_fields=[]))

M.append(dict(
    id="xr300-rally", name="Honda XR300 Rally", segment="adventure", division="BigWing", fuel="petrol", status="pre_booking",
    official_url=f"{H}/motorcycle/xr300rally",
    price=price(208000, as_of="2026-10-12", src=AC),
    variants=[v("Standard", 208000)],
    specs=dict(engine="293.5cc air-cooled single with oil-cooler", displacement_cc=293.5),
    aliases=["xr300 rally", "xr rally", "rally 300"],
    pitch="Rally-styled XR300 aimed at long-distance trail touring.",
    sources=["https://www.honda2wheelersindia.com/news/honda-motorcycle-scooter-india-unveils-10-product-made-in-india", AC],
    unverified_fields=["engine"]))

M.append(dict(
    id="cb500", name="Honda CB500", segment="heritage-roadster", division="BigWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/motorcycle/CB500",
    price=price(275000, 302000, as_of="2026-10-12", src=AC),
    variants=[v("Alloy", 275000, inferred=True), v("Spoke tubeless"), v("Spoke tubeless with graphics", 302000, inferred=True)],
    specs=dict(engine="501cc single-cylinder, air-cooled with oil-cooler", displacement_cc=501),
    aliases=["cb500", "cb 500", "c b 500"],
    pitch="Heritage roadster with a torquey 501cc single.",
    sources=["https://www.honda2wheelersindia.com/news/honda-motorcycle-scooter-india-unveils-10-product-made-in-india", AC],
    unverified_fields=[]))

M.append(dict(
    id="rebel-500", name="Honda Rebel 500", segment="cruiser", division="BigWing", fuel="petrol", status="upcoming",
    official_url=None,
    price=price(440000, 460000, as_of="2026-10-12", src=AC, notes="Expected price; expected launch Nov 2026"),
    variants=[v("Standard"), v("E-Clutch")],
    specs=dict(engine="471cc liquid-cooled parallel-twin", displacement_cc=471),
    aliases=["rebel 500"],
    pitch="Upcoming mid-size twin-cylinder cruiser; take a callback lead, no test rides yet.",
    sources=["https://www.honda2wheelersindia.com/news/honda-motorcycle-scooter-india-unveils-10-product-made-in-india", AC],
    unverified_fields=[]))

M.append(dict(
    id="nx500", name="Honda NX500 E-Clutch", segment="adventure-tourer", division="BigWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/motorcycle/nx-500-E-Clutch",
    price=price(750000, as_of="2026-10-09", src=AC, notes="Autocar India expert answer says 'nearly Rs 7.50 lakh'; June 2026 list showed Rs 6.33 lakh (pre E-Clutch). Confirm."),
    variants=[v("E-Clutch")],
    specs=dict(engine="471cc liquid-cooled parallel-twin", displacement_cc=471, max_power="35 kW @ 8600 rpm",
               max_torque="43 Nm @ 6500 rpm", transmission="6-speed with Honda E-Clutch", fuel_tank_l=17.5),
    aliases=["nx500", "nx 500", "n x 500"],
    pitch="Road-biased adventure tourer with a smooth 471cc twin and Honda E-Clutch.",
    sources=[AC, "https://www.autocarindia.com/bikes/honda-bikes/cb300r"],
    unverified_fields=["max_power", "max_torque", "fuel_tank_l"]))

M.append(dict(
    id="cb750-hornet", name="Honda CB750 Hornet E-Clutch", segment="street", division="BigWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/motorcycle/CB750-Hornet-E-Clutch",
    price=price(1049000, as_of="2026-10-12", src=AC),
    variants=[v("E-Clutch", 1049000)],
    specs=dict(engine="755cc liquid-cooled parallel-twin", displacement_cc=755, max_power="67.5 kW @ 9500 rpm",
               max_torque="75 Nm @ 7250 rpm", transmission="6-speed with Honda E-Clutch"),
    aliases=["cb750", "hornet 750", "cb 750 hornet"],
    pitch="755cc twin-cylinder street-naked with E-Clutch.",
    sources=[AC],
    unverified_fields=["max_power", "max_torque"]))

M.append(dict(
    id="xl750-transalp", name="Honda XL750 Transalp E-Clutch", segment="adventure-tourer", division="BigWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/motorcycle/xl750-transalp-E-Clutch",
    price=price(1181000, as_of="2026-06-30", src="https://www.autocarindia.com/bikes/honda-bikes/cb300r",
                notes="June 2026 list; launched Jun 2025 at Rs 10,99,990 ex-showroom Gurugram"),
    variants=[v("E-Clutch")],
    specs=dict(engine="755cc liquid-cooled parallel-twin", displacement_cc=755, max_power="67.5 kW @ 9500 rpm",
               max_torque="75 Nm @ 7250 rpm", transmission="6-speed, assist & slipper clutch",
               riding_modes=["Sport", "Standard", "Rain", "Gravel", "User"], abs="Dual-channel",
               front_suspension="Showa 43mm SFF-CA USD", wheels="21-inch front / 18-inch rear spoke",
               front_brake="Dual 310 mm wave discs", rear_brake="256 mm disc", display="5-inch TFT with Honda RoadSync"),
    colours=["Ross White", "Graphite Black"],
    aliases=["transalp", "xl750", "trans alp"],
    pitch="Do-it-all adventure tourer: 755cc twin, 5 riding modes, 21/18-inch wheels.",
    sources=["https://www.honda2wheelersindia.com/news/honda-motorcycle-scooter-india-launches-2025-xl750-transalp-bookings-open",
             "https://www.autocarindia.com/bikes/honda-bikes/cb300r"],
    unverified_fields=[]))

M.append(dict(
    id="cb1000-hornet-sp", name="Honda CB1000 Hornet SP", segment="street", division="BigWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/motorcycle/cb1000-hornet-sp",
    price=price(1329000, as_of="2026-10-12", src=AC),
    variants=[v("SP", 1329000)],
    specs=dict(engine="999cc liquid-cooled inline-four", displacement_cc=999),
    notes=["Voluntary recall (Nov 2025) for change-pedal pivot bolt on certain 2025 units; fixed free at BigWing Topline dealers."],
    aliases=["hornet 1000", "cb1000", "thousand hornet"],
    pitch="Litre-class inline-four naked in SP spec.",
    sources=[AC, "https://www.honda2wheelersindia.com/news/honda-motorcycle-scooter-india-announces-market-campaign-for-cb1000-hornet-sp"],
    unverified_fields=[]))

M.append(dict(
    id="cbr1000rr-r-sp", name="Honda CBR1000RR-R Fireblade SP", segment="superbike", division="BigWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/motorcycle/cbr1000rr-r-sp",
    price=price(3350000, as_of="2026-10-12", src=AC),
    variants=[v("SP", 3350000)],
    specs=dict(engine="999cc liquid-cooled inline-four", displacement_cc=999),
    aliases=["fireblade", "cbr1000", "cbr 1000 rr r", "fire blade"],
    pitch="Honda's flagship superbike.",
    sources=[AC],
    unverified_fields=[]))

M.append(dict(
    id="gold-wing-tour", name="Honda Gold Wing Tour", segment="luxury-tourer", division="BigWing", fuel="petrol", status="on_sale",
    official_url=f"{H}/motorcycle/gold-wing-tour",
    price=price(4282000, as_of="2026-01-31", src="https://www.autocarindia.com/bikes-images/honda-bikes/grazia",
                notes="Autocar Jan 2026 list Rs 42.82 lakh; ZigWheels (Oct 2026) shows Rs 44.30 lakh. Confirm."),
    variants=[v("Tour DCT")],
    specs=dict(engine="1,833cc flat-six", displacement_cc=1833, transmission="Automatic (DCT)"),
    aliases=["gold wing", "goldwing", "gold wing tour"],
    pitch="Flagship luxury tourer with a 1,833cc flat-six and automatic DCT.",
    sources=[AC, "https://www.zigwheels.com/newbikes/Honda/2022-cb1000r"],
    unverified_fields=["transmission"]))

# ---------------- write ----------------
os.makedirs(OUT, exist_ok=True)
index = []
for m in M:
    m.setdefault("brand", "Honda")
    m.setdefault("market", "India")
    m.setdefault("colours", [])
    m.setdefault("features", [])
    m.setdefault("notes", [])
    m["snapshot_date"] = SNAP
    m["pitch_status"] = "draft - review before use"
    ordered = {k: m[k] for k in ["id", "name", "brand", "market", "segment", "division", "fuel", "status", "official_url",
                                 "price", "variants", "specs", "features", "colours", "aliases", "pitch", "pitch_status",
                                 "notes", "sources", "unverified_fields", "snapshot_date"] if k in m}
    for k in m:
        if k not in ordered: ordered[k] = m[k]
    with open(f"{OUT}/{m['id']}.json", "w", encoding="utf-8") as f:
        json.dump(ordered, f, ensure_ascii=False, indent=2)
    p = m["price"]
    index.append({"id": m["id"], "name": m["name"], "segment": m["segment"], "fuel": m["fuel"], "status": m["status"],
                  "price_min": p["min"], "price_max": p["max"], "price_as_of": p["as_of"],
                  "unverified_fields": len(m["unverified_fields"])})

with open(f"{OUT}/_index.json", "w", encoding="utf-8") as f:
    json.dump({"snapshot_date": SNAP, "count": len(index), "models": index}, f, ensure_ascii=False, indent=2)
print(len(M), "files written")
