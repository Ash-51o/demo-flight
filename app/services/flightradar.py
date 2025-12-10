# app/services/flightradar.py
import re
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup, Tag
from app.utils.http import make_session

_FR24_BASE = "https://www.flightradar24.com/data/aircraft/"

def _clean(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

def _none_if_blank(s: Optional[str]) -> Optional[str]:
    s = _clean(s)
    return s if s else None

def _next_details_after(label: str, root: Tag) -> Optional[str]:
    for lab in root.find_all("label"):
        if _clean(lab.get_text()).upper() == label.upper():
            span = lab.find_next("span", class_="details")
            return _none_if_blank(span.get_text() if span else None)
    return None

def _airport_cell(td: Optional[Tag]) -> Dict[str, Optional[str]]:
    if not td:
        return {"city": None, "code": None}
    a = td.find("a")
    code = _clean(a.get_text()).strip("()") if a else None
    text_full = _clean(td.get_text(" "))
    city = _clean(re.sub(r"\([A-Z0-9]{3,4}\)", "", text_full)) or None
    return {"city": city or None, "code": code or None}

def get_aircraft_and_flights(registration: str, timeout: int = 30) -> Dict[str, Any]:
    reg = registration.strip().upper()
    if not reg.startswith("N"):
        reg = "N" + reg
    url = _FR24_BASE + reg
    sess = make_session()
    r = sess.get(url, timeout=timeout)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    info = soup.find("div", id="cnt-aircraft-info")
    aircraft = {"registration": reg}
    airline_code = operator_code = serial_msn = None

    if info:
        aircraft.update({
            "model": _next_details_after("AIRCRAFT", info),
            "airline": _next_details_after("AIRLINE", info),
            "operator": _next_details_after("OPERATOR", info),
            "type_code": _next_details_after("TYPE CODE", info),
            "mode_s": _next_details_after("MODE S", info),
        })
        codes: List[str] = []
        for lab in info.find_all("label"):
            if _clean(lab.get_text()) == "Code":
                span = lab.find_next("span", class_="details")
                if span:
                    codes.append(_clean(span.get_text()))
        if codes:
            airline_code = codes[0] if len(codes) > 0 else None
            operator_code = codes[1] if len(codes) > 1 else airline_code
        serial_msn = _next_details_after("SERIAL NUMBER (MSN)", info)

    aircraft["airline_code"] = airline_code
    aircraft["operator_code"] = operator_code
    aircraft["serial_msn"] = serial_msn

    flights: List[Dict[str, Any]] = []
    for tr in soup.select("#tbl-datatable tbody tr.data-row"):
        # date (also has data-timestamp)
        date_td = tr.find("td", attrs={"data-time-format": True})
        date_txt = _clean(date_td.get_text()) if date_td else None
        date_epoch = None
        if date_td and date_td.has_attr("data-timestamp"):
            try:
                date_epoch = int(date_td["data-timestamp"])
            except Exception:
                date_epoch = None

        # from/to
        route_tds = tr.select('td.hidden-xs.hidden-sm[title]')
        from_obj = _airport_cell(route_tds[0] if len(route_tds) > 0 else None)
        to_obj   = _airport_cell(route_tds[1] if len(route_tds) > 1 else None)

        # callsign
        flight_a = tr.select_one('td.hidden-xs.hidden-sm a[href*="/data/flights/"]')
        callsign = _clean(flight_a.get_text()) if flight_a else None

        # flight time (next desktop td after callsign cell)
        flight_time = None
        if flight_a:
            parent_td = flight_a.find_parent("td")
            sib = parent_td.find_next_sibling("td") if parent_td else None
            while sib and not ({"hidden-xs", "hidden-sm"} <= set(sib.get("class") or [])):
                sib = sib.find_next_sibling("td")
            if sib:
                flight_time = _clean(sib.get_text())

        # STD/ATD/STA text + epochs
        time_cells = tr.select('td.hidden-xs.hidden-sm[data-timestamp]:not([data-time-format])')
        def _cell(i):
            return _clean(time_cells[i].get_text()) if len(time_cells) > i else None
        def _epoch(i):
            if len(time_cells) > i and time_cells[i].has_attr("data-timestamp"):
                try:
                    return int(time_cells[i]["data-timestamp"])
                except Exception:
                    return None
            return None
        std, atd, sta = _cell(0), _cell(1), _cell(2)
        std_epoch, atd_epoch, sta_epoch = _epoch(0), _epoch(1), _epoch(2)

        # status text
        status_td = tr.select_one('td.hidden-xs.hidden-sm[data-prefix]')
        status = _clean(status_td.get_text()) if status_td else None

        # state color
        state_div = tr.select_one(".state-block")
        state_color = None
        if state_div:
            for c in ("red","yellow","green"):
                if c in (state_div.get("class") or []):
                    state_color = c
                    break

        flights.append({
            "date_local": date_txt,
            "date_epoch": date_epoch,
            "from": from_obj,
            "to": to_obj,
            "callsign": callsign,
            "flight_time": flight_time,
            "std": std, "atd": atd, "sta": sta,
            "std_epoch": std_epoch, "atd_epoch": atd_epoch, "sta_epoch": sta_epoch,
            "status": status,
            "state": state_color,
        })

    return {"aircraft": aircraft, "flights": flights, "source": url}
