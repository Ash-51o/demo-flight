#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

import pandas as pd
from app.services.flightradar import get_aircraft_and_flights  # source of airline name

# -------------------------------------------------------------------
# 📌 Workbook path
# -------------------------------------------------------------------
WORKBOOK_PATH = Path(__file__).resolve().parents[1] / "data" / "airline_fbo_data.xlsx"

# -------------------------------------------------------------------
# 📌 Column names
# -------------------------------------------------------------------
COL_FIRST = "First Name"
COL_LAST = "Last Name"
COL_TITLE = "Title"

COL_COMP = "Company Name"
COMPANY_COLUMNS = ["Company Name", "company_name"]

COL_EMAIL = "Email"
COL_CORP_PHONE = "Corporate Phone"   # 👈 make sure this matches your header exactly

# Kept for possible future use, but not used in output now:
COL_COMP_EM = "Company Name for Emails"
COL_EMAIL_STATUS = "Email Status"
COL_EMAIL_SRC = "Primary Email Source"
COL_EMAIL_VERIF = "Primary Email Verification Source"
COL_EMAIL_CONF = "Email Confidence"
COL_CATCH_ALL = "Primary Email Catch-all Status"
COL_LAST_VERIF = "Primary Email Last Verified At"

# -------------------------------------------------------------------
# 📌 Role detection patterns
# -------------------------------------------------------------------
DOM_PATTERNS = [
    r"\bDOM\b",
    r"director\s+of\s+maintenance",
    r"maintenance\s+director",
    r"principal\s+maintenance",
]

OCC_PATTERNS = [
    r"\bOCC\b",
    r"operations\s+control\s+center",
    r"network\s+operations\s+center",
    r"dispatch(er)?",
    r"flight\s+control",
]

# -------------------------------------------------------------------
# 📌 Helpers
# -------------------------------------------------------------------
def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()

def _match_role(title: str, patterns: List[str]) -> bool:
    t = _norm(title)
    for pat in patterns:
        if re.search(pat, t):
            return True
    return False

def _full_name(row: pd.Series) -> str:
    fn = str(row.get(COL_FIRST, "")).strip()
    ln = str(row.get(COL_LAST, "")).strip()
    return " ".join(x for x in [fn, ln] if x)

def _row_to_contact(row: pd.Series) -> Dict[str, Any]:
    """Return only the fields we care about for the output."""
    return {
        "name": _full_name(row),
        "title": str(row.get(COL_TITLE, "")).strip() or None,
        "email": str(row.get(COL_EMAIL, "")).strip() or None,
        "company": str(row.get(COL_COMP, "")).strip() or None,
        "corporate_phone": str(row.get(COL_CORP_PHONE, "")).strip() or None,
    }

# -------------------------------------------------------------------
# 📌 Load ALL sheets
# -------------------------------------------------------------------
def _load_frames() -> List[pd.DataFrame]:
    if not WORKBOOK_PATH.exists():
        raise FileNotFoundError(f"Workbook not found at: {WORKBOOK_PATH}")

    xls = pd.ExcelFile(WORKBOOK_PATH)
    frames: List[pd.DataFrame] = []

    for name in xls.sheet_names:
        df = xls.parse(name).fillna("")
        frames.append(df)

    if not frames:
        raise ValueError(f"No sheets found in workbook: {WORKBOOK_PATH}")

    return frames

# -------------------------------------------------------------------
# 🔍 CORE LOOKUP
# -------------------------------------------------------------------
def find_dom_occ_for_airline(airline_name: str) -> Dict[str, Any]:
    frames = _load_frames()
    dom_list, occ_list, other_list = [], [], []

    query = _norm(airline_name)

    for df in frames:
        mask = None
        for col in COMPANY_COLUMNS:
            if col in df.columns:
                col_mask = df[col].astype(str).str.lower().str.contains(query, na=False)
                mask = col_mask if mask is None else (mask | col_mask)

        if mask is None:
            continue

        subset = df[mask]
        if subset.empty:
            continue

        for _, row in subset.iterrows():
            title = str(row.get(COL_TITLE, "")).strip()
            contact = _row_to_contact(row)
            if _match_role(title, DOM_PATTERNS):
                dom_list.append(contact)
            elif _match_role(title, OCC_PATTERNS):
                occ_list.append(contact)
            else:
                other_list.append(contact)

    return {
        "airline": airline_name,
        "dom": dom_list,
        "occ": occ_list,
        "other": other_list,
        "workbook_used": str(WORKBOOK_PATH),
    }

# -------------------------------------------------------------------
# 🔧 CLI ENTRY POINT
# -------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Lookup DOM/OCC contacts for an airline or fetch airline via tail using Flightradar."
    )
    ap.add_argument("--airline", help="Airline name to search (case-insensitive)")
    ap.add_argument("--tail", help="Tail number to fetch airline info from Flightradar (e.g., N12345)")
    ap.add_argument("--as-json", action="store_true", help="Output as JSON")
    args = ap.parse_args()

    if args.airline:
        result = find_dom_occ_for_airline(args.airline)
    elif args.tail:
        try:
            fr_data = get_aircraft_and_flights(args.tail)
            aircraft = fr_data.get("aircraft", {}) or {}
            operator = aircraft.get("operator") or aircraft.get("airline")
            if not operator:
                raise ValueError("No operator found for the given tail number.")
            result = find_dom_occ_for_airline(operator)
            result["source_operator"] = operator
        except Exception as e:
            print(f"Error fetching operator from Flightradar: {e}")
            return
    else:
        print("Please provide either --airline or --tail")
        return

    if args.as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"Airline: {result['airline']}")
        print(f"DOM ({len(result['dom'])}): {result['dom']}")
        print(f"OCC ({len(result['occ'])}): {result['occ']}")

if __name__ == "__main__":
    main()
