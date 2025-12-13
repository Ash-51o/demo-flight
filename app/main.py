# app/test.py

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


app = FastAPI(title="Airlift – GA Targeting Helper (test)", version="0.4.0")

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
    n_norm = normalize_n(n)
    if not n_norm:
        raise HTTPException(400, "Provide a valid N-number.")

    # FAA/FlightAware
    reg = get_registration(n_norm)

    # FR24
    fr = get_aircraft_and_flights(n_norm)
    fr_ac = fr.get("aircraft", {}) or {}
    flights = fr.get("flights", []) or []

    # ADS-B (optional)
    # prefer a hex from FR24/registry; registry "Mode S Code" may include octal/hex as "octal / HEX"
    hex_from_fr = fr_ac.get("mode_s")
    hex_from_reg = None
    raw = reg.get("mode_s_code") or ""
    if "/" in raw:
        hex_from_reg = raw.split("/")[-1].strip()
    icao_hex = hex_from_fr or hex_from_reg

    # ---------------------------
    # HARDCODED ADS-B PROFILES
    # ---------------------------
    HARDCODED_ADSB_PROFILES = {
        "N103DY": {
            "callsign": "N103DY",
            "hex": "A8C31F",
            "registration": "N103DY",
            "icao_type": "GLF6",
            "type_full": "Gulfstream G600",
            "type_desc": "Business Jet",
            "category": "A3",
            "baro_altitude": 43000,
            "groundspeed_kt": 445,
            "ground_track": 210,
            "true_heading": 208,
            "mag_heading": 205,
            "squawk": "1200",
            "position": {"lat": 34.729, "lon": -86.586},
            "last_seen": "2024-12-12T14:31:00Z",
            "last_pos_age": 11,
            "pos_epoch": 1734004260,
            "message_rate": 5.4,
            "source": "-",
        },

        "N605FX": {
            "callsign": "LXJ605",
            "hex": "A7DA65",
            "registration": "N605FX",
            "icao_type": "C750",
            "type_full": "Cessna Citation X",
            "type_desc": "Business Jet",
            "category": "A3",
            "baro_altitude": 41000,
            "groundspeed_kt": 430,
            "ground_track": 274,
            "true_heading": 273,
            "mag_heading": 271,
            "squawk": "1200",
            "position": {"lat": 32.8968, "lon": -97.0379},
            "last_seen": "2024-12-12T14:30:00Z",
            "last_pos_age": 12,
            "pos_epoch": 1734004200,
            "message_rate": 5.2,
            "source": "-",
        },

        "N101CN": {
            "callsign": "N101CN",
            "hex": "A0F4C1",
            "registration": "N101CN",
            "icao_type": "PC12",
            "type_full": "Pilatus PC-12 NG",
            "type_desc": "Single-engine Turboprop",
            "category": "A2",
            "baro_altitude": 28000,
            "groundspeed_kt": 255,
            "ground_track": 147,
            "true_heading": 146,
            "mag_heading": 143,
            "squawk": "4275",
            "position": {"lat": 39.8561, "lon": -104.6737},
            "last_seen": "2024-12-12T14:32:30Z",
            "last_pos_age": 6,
            "pos_epoch": 1734004350,
            "message_rate": 4.1,
            "source": "-",
        },

        "N780NC": {
        "callsign": "EJA780",            
        "hex": "A9D541",                 
        "registration": "N780NC",
        "icao_type": "C56X",
        "type_full": "Cessna Citation Excel/XLS",
        "type_desc": "Business Jet",
        "category": "A3",
        "baro_altitude": 41000,
        "groundspeed_kt": 446,
        "ground_track": 228,
        "true_heading": 226,
        "mag_heading": 224,
        "squawk": "5423",
        "position": {"lat": 33.6407, "lon": -84.4277},   
        "last_seen": "2024-12-12T14:36:20Z",
        "last_pos_age": 5,
        "pos_epoch": 1734004580,
        "message_rate": 5.3,
        "source": "-",
    },

        "N525FX": {
            "callsign": "LXJ525",
            "hex": "A63F22",
            "registration": "N525FX",
            "icao_type": "E55P",
            "type_full": "Embraer Phenom 300",
            "type_desc": "Light Jet",
            "category": "A2",
            "baro_altitude": 39000,
            "groundspeed_kt": 420,
            "ground_track": 301,
            "true_heading": 299,
            "mag_heading": 296,
            "squawk": "4512",
            "position": {"lat": 36.1245, "lon": -86.6782},
            "last_seen": "2024-12-12T14:35:00Z",
            "last_pos_age": 3,
            "pos_epoch": 1734004500,
            "message_rate": 5.6,
            "source": "-",
        },

        
    }

    # lookup ADS-B by normalized N-number
    adsb_data = get_adsb_panel(icao_hex) if use_adsb and icao_hex else {}
    # adsb_data = HARDCODED_ADSB_PROFILES.get(n_norm, {})
    print(f"ADS-B data for {n_norm} (hex={icao_hex}): {adsb_data}")
    # coerce pos_epoch to int if possible
    pos_epoch = None
    try:
        if adsb_data.get("pos_epoch"):
            pos_epoch = int(str(adsb_data["pos_epoch"]).strip())
    except Exception:
        pos_epoch = None

    # helper to coerce values to strings for the Pydantic model
    def _str_or_none(v):
        if v is None:
            return None
        # format position dict as "lat, lon"
        if isinstance(v, dict):
            lat = v.get("lat")
            lon = v.get("lon")
            try:
                return f"{float(lat):.6f}, {float(lon):.6f}"
            except Exception:
                return str(v)
        return str(v)

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
        callsign=_str_or_none(adsb_data.get("callsign") or adsb_data.get("registration")),
        hex=_str_or_none(adsb_data.get("hex") or icao_hex),
        registration=_str_or_none(adsb_data.get("registration")),
        icao_type=_str_or_none(adsb_data.get("icao_type")),
        type_full=_str_or_none(adsb_data.get("type_full")),
        type_desc=_str_or_none(adsb_data.get("type_desc")),
        owners_ops=_str_or_none(adsb_data.get("owners_ops")),
        squawk=_str_or_none(adsb_data.get("squawk")),
        groundspeed_kt=_str_or_none(adsb_data.get("groundspeed_kt")),
        baro_altitude=_str_or_none(adsb_data.get("baro_altitude")),
        ground_track=_str_or_none(adsb_data.get("ground_track")),
        true_heading=_str_or_none(adsb_data.get("true_heading")),
        mag_heading=_str_or_none(adsb_data.get("mag_heading")),
        mach=_str_or_none(adsb_data.get("mach")),
        category=_str_or_none(adsb_data.get("category")),
        position=_str_or_none(adsb_data.get("position")),
        last_seen=_str_or_none(adsb_data.get("last_seen")),
        last_pos_age=_str_or_none(adsb_data.get("last_pos_age")),
        source=_str_or_none(adsb_data.get("source")),
        message_rate=_str_or_none(adsb_data.get("message_rate")),
        pos_epoch=pos_epoch,
    )

    inferred_op, is_fractional = infer_operation(
        registry_block.owner, fr24_block.operator, registry_block.fractional_owner
    )

    # Top airports
    top7 = _top_airports(flights, 7)
    top30 = _top_airports(flights, 30)
    top90 = _top_airports(flights, 90)  # uses available rows (FR24 page depth)

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
    # Run the `app` in this module. When invoking the file directly,
    # point uvicorn at `app.main:app` so the correct FastAPI instance is used.
    uvicorn.run("main:app", host="0.0.0.0", port=9001, reload=True)
