# app/services/adsb_globe.py
# Scrapes ADS-B Exchange "globe" dynamic panel (headless Chrome) using the ICAO hex.
# Use responsibly and comply with site ToS/licensing.

from typing import Optional, Dict
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

GLOBE_URL = "https://globe.adsbexchange.com/?icao={hex}"

def _safe_text(soup: BeautifulSoup, el_id: str) -> Optional[str]:
    el = soup.find(['span', 'div'], id=el_id)
    txt = el.get_text(strip=True) if el else None
    return txt or None

def get_adsb_panel(icao_hex: str, timeout: int = 20) -> Dict:
    """
    Returns a dict of key fields from ADS-B Exchange panel.
    """
    hx = (icao_hex or "").strip().lower()
    if not hx:
        return {}

    url = GLOBE_URL.format(hex=hx)

    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    try:
        driver.get(url)
        WebDriverWait(driver, timeout).until(
            lambda d: d.find_element(By.ID, "selected_icao").text.strip() != "" and
                      d.find_element(By.ID, "selected_icao").text.strip().lower().find(hx) >= 0
        )
        # wait a bit longer for the rest to hydrate
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.ID, "selected_registration"))
        )

        soup = BeautifulSoup(driver.page_source, "html.parser")
        return {
            "callsign": _safe_text(soup, "selected_callsign"),
            "hex": (_safe_text(soup, "selected_icao") or "").replace("Hex:", "").strip(),
            "registration": _safe_text(soup, "selected_registration"),
            "country": _safe_text(soup, "selected_country"),
            "icao_type": _safe_text(soup, "selected_icaotype"),
            "type_full": _safe_text(soup, "selected_typelong"),
            "type_desc": _safe_text(soup, "selected_typedesc"),
            "owners_ops": _safe_text(soup, "selected_ownop"),

            "squawk": _safe_text(soup, "selected_squawk1"),
            "groundspeed_kt": _safe_text(soup, "selected_speed1"),
            "baro_altitude": _safe_text(soup, "selected_altitude1"),
            "ground_track": _safe_text(soup, "selected_track1"),
            "true_heading": _safe_text(soup, "selected_true_heading"),
            "mag_heading": _safe_text(soup, "selected_mag_heading"),
            "mach": _safe_text(soup, "selected_mach"),
            "category": _safe_text(soup, "selected_category"),

            "position": _safe_text(soup, "selected_position"),  # "lat°, lon°"
            "last_seen": _safe_text(soup, "selected_seen"),      # e.g., "5 h"
            "last_pos_age": _safe_text(soup, "selected_seen_pos"),
            "source": _safe_text(soup, "selected_source"),
            "message_rate": _safe_text(soup, "selected_message_rate"),
            "pos_epoch": _safe_text(soup, "selected_pos_epoch"),  # epoch seconds as text
        }
    except Exception as e:
        # print(f"[ADS-B] Error fetching {url}: {e}")
        return {}
    finally:
        driver.quit()
