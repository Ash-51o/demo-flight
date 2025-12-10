# app/models/schemas.py
from typing import List, Optional
from pydantic import BaseModel

class AirportHit(BaseModel):
    code: Optional[str]
    count: int

class FlightRow(BaseModel):
    date_local: Optional[str]
    from_airport: Optional[str]
    to_airport: Optional[str]
    callsign: Optional[str]
    flight_time: Optional[str]

class FR24Info(BaseModel):
    model: Optional[str]
    airline: Optional[str]
    operator: Optional[str]
    type_code: Optional[str]
    airline_code: Optional[str]
    operator_code: Optional[str]
    mode_s: Optional[str]
    serial_msn: Optional[str]
    source_url: Optional[str]

class RegistryInfo(BaseModel):
    owner: Optional[str]
    status: Optional[str]
    airworthiness_class: Optional[str]
    certificate_issue_date: Optional[str]
    airworthiness_date: Optional[str]
    expiration: Optional[str]
    engine: Optional[str]
    serial_number: Optional[str]
    model_year: Optional[str]
    fractional_owner: Optional[bool]
    seats: Optional[str]
    engines_count: Optional[str]
    source_url: Optional[str]

class ADSBInfo(BaseModel):
    callsign: Optional[str]
    hex: Optional[str]
    registration: Optional[str]
    icao_type: Optional[str]
    type_full: Optional[str]
    type_desc: Optional[str]
    owners_ops: Optional[str]
    squawk: Optional[str]
    groundspeed_kt: Optional[str]
    baro_altitude: Optional[str]
    ground_track: Optional[str]
    true_heading: Optional[str]
    mag_heading: Optional[str]
    mach: Optional[str]
    category: Optional[str]
    position: Optional[str]
    last_seen: Optional[str]
    last_pos_age: Optional[str]
    source: Optional[str]
    message_rate: Optional[str]
    pos_epoch: Optional[int]

class Links(BaseModel):
    fr24_url: Optional[str]
    registry_source_url: Optional[str]
    adsb_globe_url: Optional[str]

class LastSpotted(BaseModel):
    place_code: Optional[str]
    place_city: Optional[str]
    epoch: Optional[int]
    source: Optional[str]

class OperatingBase(BaseModel):
    code: Optional[str]
    confidence: Optional[float]  # 0..1

class OvernightStat(BaseModel):
    airport: Optional[str]
    overnights: int
    avg_ground_hours: float

class ChaseScore(BaseModel):
    score: int
    reasons: List[str]

class AircraftInsight(BaseModel):
    tail_number: str
    fr24: FR24Info
    registry: RegistryInfo
    adsb: ADSBInfo
    links: Links

    inferred_operation: Optional[str]
    is_fractional: Optional[bool]
    buyer_roles_hint: List[str]

    last_spotted: LastSpotted
    top_airports_7d: List[AirportHit]
    top_airports_30d: List[AirportHit]
    top_airports_90d: List[AirportHit]
    recent_flights: List[FlightRow]

    likely_base: OperatingBase
    overnights_top: List[OvernightStat]
    chase: ChaseScore
