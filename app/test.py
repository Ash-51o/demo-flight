# app/main.py

import collections
import datetime as dt
from typing import List, Tuple, Optional, Dict

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.services.flightaware import get_registration
from app.services.flightradar import get_aircraft_and_flights
from app.services.adsb_globe import get_adsb_panel, GLOBE_URL
from app.models.schemas import (
    AircraftInsight,
    AirportHit,
    FlightRow,
    FR24Info,
    RegistryInfo,
    ADSBInfo,
    Links,
    LastSpotted,
    OperatingBase,
    OvernightStat,
    ChaseScore,
)
from app.services.getcontacts import find_dom_occ_for_airline


app = FastAPI(title="Airlift – GA Targeting Helper", version="0.4.0")

# static/index.html + static/app.js
app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*", "http://127.0.0.1:9001", "http://localhost:9001"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRACTIONAL_BRANDS = {
    "NETJETS",
    "FLEXJET",
    "WHEELS UP",
    "XOJET",
    "VISTAJET",
    "PLANESENSE",
    "JET LINX",
    "JET EDGE",
    "ONEFLIGHT",
    "MAGELLAN",
    "CLAY LACY",
    "SENTIENT",
}


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
def normalize_n(n: str) -> str:
    n = (n or "").strip().upper().lstrip("#")
    if not n:
        return ""
    return n if n.startswith("N") else "N" + n


def infer_operation(
    owner: Optional[str],
    operator: Optional[str],
    fractional_flag: Optional[bool],
) -> Tuple[str, bool]:
    owner_u = (owner or "").upper()
    oper_u = (operator or "").upper()
    fractional = bool(fractional_flag)
    brand_hit = any(b in owner_u or b in oper_u for b in FRACTIONAL_BRANDS)
    if fractional or brand_hit:
        return ("Part 135 – Fractional/Charter (fractional/managed)", True)
    return ("Part 91 – Corporate/Private", False)


def _top_airports(flights: List[dict], days: int) -> List[AirportHit]:
    now = dt.datetime.utcnow().timestamp()
    cutoff = now - days * 86400
    counter = collections.Counter()
    for f in flights:
        if f.get("date_epoch") and f["date_epoch"] < cutoff:
            continue
        for k in ("from", "to"):
            code = (f.get(k) or {}).get("code")
            if code:
                counter[code] += 1
    return [AirportHit(code=c, count=n) for c, n in counter.most_common(8)]


def _derive_likely_base_and_overnights(
    flights: List[dict],
) -> Tuple[OperatingBase, List[OvernightStat]]:
    """
    Uses FR24 rows newest->oldest; we sort ascending for ground-time pairing.
    - Base: airport with max visits (arrivals+departures) over all rows; confidence = share of visits.
    - Overnights: ground time >= 8h between STA of flight i and ATD of next flight j at same airport.
    """
    if not flights:
        return OperatingBase(code=None, confidence=None), []

    # count visits
    counts = collections.Counter()
    for f in flights:
        for k in ("from", "to"):
            code = (f.get(k) or {}).get("code")
            if code:
                counts[code] += 1
    total_visits = sum(counts.values()) or 1
    base_code, base_cnt = (None, 0)
    if counts:
        base_code, base_cnt = counts.most_common(1)[0]
    base = OperatingBase(
        code=base_code,
        confidence=(base_cnt / total_visits if base_cnt else None),
    )

    # ground times
    rows = sorted(
        [f for f in flights if f.get("date_epoch") is not None],
        key=lambda x: x["date_epoch"],
    )
    ground_buckets: Dict[str, list] = collections.defaultdict(list)
    for i in range(len(rows) - 1):
        a = rows[i]
        b = rows[i + 1]
        to_code = (a.get("to") or {}).get("code")
        from_code = (b.get("from") or {}).get("code")
        if not to_code or not from_code or to_code != from_code:
            continue
        sta_epoch = a.get("sta_epoch")
        atd_epoch = b.get("atd_epoch")
        if sta_epoch and atd_epoch and atd_epoch > sta_epoch:
            ground_sec = atd_epoch - sta_epoch
            ground_buckets[to_code].append(ground_sec)

    stats: List[OvernightStat] = []
    for ap, durations in ground_buckets.items():
        if not durations:
            continue
        overnights = sum(1 for s in durations if s >= 8 * 3600)
        avg_hours = sum(durations) / len(durations) / 3600.0
        stats.append(
            OvernightStat(
                airport=ap,
                overnights=overnights,
                avg_ground_hours=round(avg_hours, 1),
            )
        )

    stats.sort(key=lambda s: (s.overnights, s.avg_ground_hours), reverse=True)
    return base, stats[:5]


def _compute_chase_score(
    last_epoch: Optional[int],
    is_fractional: bool,
    overnights: List[OvernightStat],
) -> ChaseScore:
    score = 0
    reasons: List[str] = []
    # last seen freshness
    if last_epoch:
        age_h = (dt.datetime.utcnow().timestamp() - last_epoch) / 3600.0
        if age_h <= 72:
            score += 1
            reasons.append("Seen in last 72h")
    # fractional/135 tend to buy faster
    if is_fractional:
        score += 1
        reasons.append("Fractional / Part 135")
    # any overnight in data
    if any(s.overnights > 0 for s in overnights):
        score += 2
        reasons.append("Overnight groundtime present")
    return ChaseScore(score=score, reasons=reasons)


def _choose_last_spotted(
    fr_last: Optional[LastSpotted],
    adsb_pos_epoch: Optional[int],
) -> LastSpotted:
    """
    Prefer ADS-B epoch if it's more recent; otherwise keep FR24-derived place.
    """
    if adsb_pos_epoch and (
        not fr_last or not fr_last.epoch or adsb_pos_epoch > fr_last.epoch
    ):
        return LastSpotted(
            place_code=fr_last.place_code if fr_last else None,
            place_city=fr_last.place_city if fr_last else None,
            epoch=adsb_pos_epoch,
            source="ADS-B Exchange (epoch)",
        )
    return fr_last or LastSpotted(
        place_code=None, place_city=None, epoch=None, source=None
    )


def _last_spotted_from_fr24(flights: List[dict]) -> LastSpotted:
    if not flights:
        return LastSpotted(
            place_code=None,
            place_city=None,
            epoch=None,
            source="FR24 flights",
        )
    f0 = flights[0]
    status = (f0.get("status") or "").lower()
    to_air = f0.get("to") or {}
    from_air = f0.get("from") or {}
    place_code = None
    place_city = None
    if "landed" in status and (to_air.get("code") or to_air.get("city")):
        place_code, place_city = to_air.get("code"), to_air.get("city")
    else:
        place_code, place_city = from_air.get("code"), from_air.get("city")
    epoch = f0.get("date_epoch")
    return LastSpotted(
        place_code=place_code,
        place_city=place_city,
        epoch=epoch,
        source="FR24 flights",
    )


# -------------------------------------------------------------------
# Routes
# -------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def root():
    # Serve the bundled static index file from the project `static` directory.
    # Use a path relative to the process working directory (project root).
    print("Serving static index.html")
    return FileResponse("static/index.html")


@app.get("/api/aircraft", response_model=AircraftInsight)
def api_aircraft(
    n: str = Query(..., description="N-number (with or without leading N)"),
    use_adsb: bool = Query(True, description="Whether to fetch ADS-B Exchange live panel"),
):
    print("\n" + "="*80)
    print(f"API REQUEST: /api/aircraft?n={n}&use_adsb={use_adsb}")
    print("="*80)
    
    n_norm = normalize_n(n)
    if not n_norm:
        raise HTTPException(400, "Provide a valid N-number.")
    
    print(f"1. Normalized tail: {n_norm}")

    # FAA/FlightAware
    print(f"\n2. Fetching FAA Registry data...")
    reg = get_registration(n_norm)
    print(f"   Registry owner: {reg.get('owner', 'N/A')}")
    print(f"   Registry mode_s_code: {reg.get('mode_s_code', 'N/A')}")

    # FR24
    print(f"\n3. Fetching FlightRadar24 data...")
    fr = get_aircraft_and_flights(n_norm)
    fr_ac = fr.get("aircraft", {}) or {}
    flights = fr.get("flights", []) or []
    print(f"   FR24 operator: {fr_ac.get('operator', 'N/A')}")
    print(f"   FR24 mode_s: {fr_ac.get('mode_s', 'N/A')}")
    print(f"   FR24 flights count: {len(flights)}")

    # ADS-B (optional)
    print(f"\n4. Determining ICAO hex code...")
    hex_from_fr = fr_ac.get("mode_s")
    hex_from_reg = None
    raw = reg.get("mode_s_code") or ""
    if "/" in raw:
        hex_from_reg = raw.split("/")[-1].strip()
    
    print(f"   Hex from FR24: {hex_from_fr}")
    print(f"   Hex from Registry raw: {raw}")
    print(f"   Hex from Registry parsed: {hex_from_reg}")
    
    icao_hex = hex_from_fr or hex_from_reg
    print(f"   >>> FINAL ICAO HEX: {icao_hex}")
    
    # Fetch ADS-B data
    print(f"\n5. Fetching ADS-B Exchange data...")
    print(f"   use_adsb={use_adsb}, icao_hex={icao_hex}")
    
    adsb_data = {}
    if use_adsb and icao_hex:
        print(f"   Calling get_adsb_panel('{icao_hex}')...")
        try:
            adsb_data = get_adsb_panel(icao_hex)
            print(f"   ✓ ADS-B call completed")
            print(f"   Raw ADS-B data keys: {list(adsb_data.keys())}")
            print(f"   Raw ADS-B data sample:")
            for key in ['hex', 'registration', 'callsign', 'position', 'last_seen']:
                if key in adsb_data:
                    print(f"     - {key}: {adsb_data[key]}")
        except Exception as e:
            print(f"   ✗ ADS-B call failed: {e}")
            import traceback
            traceback.print_exc()
    else:
        if not use_adsb:
            print(f"   ⊘ Skipped: use_adsb is False")
        if not icao_hex:
            print(f"   ⊘ Skipped: No ICAO hex available")
    
    # Filter out 'n/a' values from ADS-B data
    print(f"\n6. Cleaning ADS-B data...")
    adsb_data_clean = {
        k: (None if v == 'n/a' else v) 
        for k, v in adsb_data.items()
    }
    non_null_count = sum(1 for v in adsb_data_clean.values() if v is not None)
    print(f"   Non-null fields after cleaning: {non_null_count}/{len(adsb_data_clean)}")
    
    # coerce pos_epoch to int if possible
    pos_epoch = None
    try:
        raw_epoch = adsb_data_clean.get("pos_epoch")
        if raw_epoch and raw_epoch != 'n/a':
            pos_epoch = int(str(raw_epoch).strip())
            print(f"   Parsed pos_epoch: {pos_epoch}")
    except (ValueError, TypeError) as e:
        print(f"   Could not parse pos_epoch: {e}")
        pos_epoch = None

    print(f"\n7. Building response blocks...")
    
    fr24_block = FR24Info(
        model=fr_ac.get("model"),
        airline=fr_ac.get("airline"),
        operator=fr_ac.get("operator"),
        type_code=fr_ac.get("type_code"),
        airline_code=fr_ac.get("airline_code"),
        operator_code=fr_ac.get("operator_code"),
        mode_s=fr_ac.get("mode_s"),
        serial_msn=fr_ac.get("serial_msn"),
        source_url=fr.get("source"),
    )

    registry_block = RegistryInfo(
        owner=reg.get("owner"),
        status=reg.get("status"),
        airworthiness_class=reg.get("airworthiness_class"),
        certificate_issue_date=reg.get("certificate_issue_date"),
        airworthiness_date=reg.get("airworthiness_date"),
        expiration=reg.get("expiration"),
        engine=reg.get("engine"),
        serial_number=reg.get("serial_number"),
        model_year=reg.get("model_year_text"),
        fractional_owner=reg.get("fractional_owner"),
        seats=reg.get("seats"),
        engines_count=reg.get("engines_count"),
        source_url=reg.get("registry_source_url"),
    )

    adsb_block = ADSBInfo(
        callsign=adsb_data_clean.get("callsign"),
        hex=adsb_data_clean.get("hex") or icao_hex,
        registration=adsb_data_clean.get("registration"),
        icao_type=adsb_data_clean.get("icao_type"),
        type_full=adsb_data_clean.get("type_full"),
        type_desc=adsb_data_clean.get("type_desc"),
        owners_ops=adsb_data_clean.get("owners_ops"),
        squawk=adsb_data_clean.get("squawk"),
        groundspeed_kt=adsb_data_clean.get("groundspeed_kt"),
        baro_altitude=adsb_data_clean.get("baro_altitude"),
        ground_track=adsb_data_clean.get("ground_track"),
        true_heading=adsb_data_clean.get("true_heading"),
        mag_heading=adsb_data_clean.get("mag_heading"),
        mach=adsb_data_clean.get("mach"),
        category=adsb_data_clean.get("category"),
        position=adsb_data_clean.get("position"),
        last_seen=adsb_data_clean.get("last_seen"),
        last_pos_age=adsb_data_clean.get("last_pos_age"),
        source=adsb_data_clean.get("source"),
        message_rate=adsb_data_clean.get("message_rate"),
        pos_epoch=pos_epoch,
    )
    
    print(f"   ADS-B block created with hex: {adsb_block.hex}")

    inferred_op, is_fractional = infer_operation(
        registry_block.owner, fr24_block.operator, registry_block.fractional_owner
    )

    # Top airports
    top7 = _top_airports(flights, 7)
    top30 = _top_airports(flights, 30)
    top90 = _top_airports(flights, 90)

    # Likely base & overnights
    base, overnight_stats = _derive_likely_base_and_overnights(flights)

    # Recent flights table (trim)
    recent_rows: List[FlightRow] = []
    for f in flights[:15]:
        recent_rows.append(
            FlightRow(
                date_local=f.get("date_local"),
                from_airport=(f.get("from") or {}).get("code"),
                to_airport=(f.get("to") or {}).get("code"),
                callsign=f.get("callsign"),
                flight_time=f.get("flight_time"),
            )
        )

    buyer_hint = (
        [
            "Director of Maintenance (DOM)",
            "Chief Pilot",
            "Fleet Manager / Aviation Dept.",
        ]
        if "Part 91" in inferred_op
        else ["OCC / Dispatch", "Base Manager (FBO/Operator)", "Director of Maintenance (DOM)"]
    )

    fr_last = _last_spotted_from_fr24(flights)
    last_spotted = _choose_last_spotted(fr_last, pos_epoch)

    links = Links(
        fr24_url=fr24_block.source_url,
        registry_source_url=registry_block.source_url,
        adsb_globe_url=(
            GLOBE_URL.format(hex=(adsb_block.hex or "")) if adsb_block.hex else None
        ),
    )

    chase = _compute_chase_score(last_spotted.epoch, is_fractional, overnight_stats)

    print(f"\n8. Response ready - returning AircraftInsight")
    print(f"   ADSB Globe URL: {links.adsb_globe_url}")
    print("="*80 + "\n")

    return AircraftInsight(
        tail_number=n_norm,
        fr24=fr24_block,
        registry=registry_block,
        adsb=adsb_block,
        links=links,
        inferred_operation=inferred_op,
        is_fractional=is_fractional,
        buyer_roles_hint=buyer_hint,
        last_spotted=last_spotted,
        top_airports_7d=top7,
        top_airports_30d=top30,
        top_airports_90d=top90,
        recent_flights=recent_rows,
        likely_base=base,
        overnights_top=overnight_stats,
        chase=chase,
    )


@app.get("/api/contacts-by-tail")
def api_contacts_by_tail(
    n: str = Query(..., description="N-number (with or without leading N)")
):
    """
    Given a tail (N-number), look up the operator/airline via FR24,
    then pull DOM/OCC contacts for that airline from the Excel workbook.
    """
    n_norm = normalize_n(n)
    if not n_norm:
        raise HTTPException(status_code=400, detail="Provide a valid N-number.")

    # Use existing flightradar service to get operator / airline for this tail
    fr = get_aircraft_and_flights(n_norm)
    ac = fr.get("aircraft", {}) or {}

    operator = ac.get("operator") or ac.get("airline")
    if not operator:
        raise HTTPException(
            status_code=404,
            detail="Could not determine operator/airline for this tail.",
        )

    contacts = find_dom_occ_for_airline(operator)

    return {
        "tail_number": n_norm,
        "airline": operator,
        "contacts": contacts,
    }


if __name__ == "__main__":
    import uvicorn
    print("Starting Uvicorn server for testing...")
    uvicorn.run("app.test:app", host="127.0.0.1", port=9001, reload=True)