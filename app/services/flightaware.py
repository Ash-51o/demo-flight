# app/services/flightaware.py
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from app.utils.http import make_session

FA_BASE = "https://www.flightaware.com"

def _text(el) -> str:
    return " ".join(el.stripped_strings) if el else ""

def _fieldset_by_legend(soup: BeautifulSoup, legend_text: str):
    for fs in soup.find_all("fieldset"):
        lg = fs.find("legend")
        if lg and _text(lg).strip().lower() == legend_text.lower():
            return fs
    return None

def _collect_rows(fieldset) -> dict:
    rows = {}
    for row in fieldset.select("div.row.attribute-row"):
        key_el = row.select_one(".title-text")
        val_el = row.select_one(".medium-3.columns")
        if key_el and val_el:
            rows[_text(key_el)] = _text(val_el)
    return rows

def get_registration(n_number: str) -> dict:
    n = n_number.strip().upper().lstrip("#")
    if not n.startswith("N"):
        n = "N" + n

    url = f"{FA_BASE}/resources/registration/{n}"
    console.log(url)
    sess = make_session()
    r = sess.get(url, timeout=25, allow_redirects=True)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    tail = soup.select_one("input#ident")
    tail_value = tail["value"] if tail and tail.has_attr("value") else n

    fs_summary = _fieldset_by_legend(soup, "Aircraft Summary")
    fs_details = _fieldset_by_legend(soup, "Registration Details")

    summary = _collect_rows(fs_summary) if fs_summary else {}
    details = _collect_rows(fs_details) if fs_details else {}

    registry_src_url = ""
    if fs_details:
        for row in fs_details.select("div.row.attribute-row"):
            title = _text(row.select_one(".title-text")).lower()
            if title == "registry source":
                a = row.select_one(".medium-3.columns a[href]")
                if a and a.get("href"):
                    registry_src_url = urljoin(url, a["href"])
                break

    model_year = ""
    if "Summary" in summary:
        parts = re.split(r"\s{2,}", summary["Summary"])
        model_year = parts[0] if parts else ""

    fractional_owner = None
    for k, v in summary.items():
        if "Fractional" in k:
            fractional_owner = "YES" in v.upper()

    # parse seats/engines from the "(12 seats / 2 engines)" ending if present
    seats = engines = None
    if "Summary" in summary and "(" in summary["Summary"] and ")" in summary["Summary"]:
        bracket = summary["Summary"].split("(")[-1].split(")")[0]
        parts = [p.strip() for p in bracket.split("/")]
        for p in parts:
            if "seats" in p:
                seats = re.sub(r"[^0-9]", "", p) or None
            if "engine" in p:
                engines = re.sub(r"[^0-9]", "", p) or None

    return {
        "tail_number": tail_value,
        "summary_text": summary.get("Summary"),
        "owner": summary.get("Owner"),
        "mode_s_code": summary.get("Mode S Code"),
        "serial_number": summary.get("Serial Number"),
        "airworthiness_class": summary.get("Airworthiness Class"),
        "engine": summary.get("Engine"),
        "weight": summary.get("Weight"),
        "status": details.get("Status"),
        "certificate_issue_date": details.get("Certificate Issue Date"),
        "airworthiness_date": details.get("Airworthiness Date"),
        "expiration": details.get("Expiration"),
        "registry_source": details.get("Registry Source"),
        "registry_source_url": registry_src_url,
        "model_year_text": model_year,
        "fractional_owner": fractional_owner,
        "seats": seats,
        "engines_count": engines,
        "source_url": url,
    }
