# app/services/adsb_globe.py
import os
import time
from typing import Optional, Dict
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

GLOBE_URL = "https://globe.adsbexchange.com/?icao={hex}"

# Cache results for 30 seconds to avoid repeated scraping
_cache = {}
_cache_timeout = 600

def _get_chrome_options():
    """Production-ready Chrome options"""
    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-software-rasterizer')
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-logging')
    options.add_argument('--log-level=3')
    options.add_argument('--silent')
    options.add_argument('--window-size=1280,720')
    options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
    
    # Memory optimization
    options.add_argument('--disable-background-networking')
    options.add_argument('--disable-background-timer-throttling')
    options.add_argument('--disable-backgrounding-occluded-windows')
    options.add_argument('--disable-breakpad')
    options.add_argument('--disable-component-extensions-with-background-pages')
    options.add_argument('--disable-features=TranslateUI,BlinkGenPropertyTrees')
    
    # For Docker/Render environments
    if os.getenv('RENDER') or os.getenv('DOCKER'):
        options.binary_location = '/usr/bin/chromium-browser'
    
    return options

def _safe_text(soup: BeautifulSoup, el_id: str) -> Optional[str]:
    """Extract text from element by ID, return None if not found or empty"""
    el = soup.find(['span', 'div'], id=el_id)
    txt = el.get_text(strip=True) if el else None
    return txt or None

def get_adsb_panel(icao_hex: str, timeout: int = 20) -> Dict:
    """
    Returns a dict of key fields from ADS-B Exchange panel.
    
    WARNING: This is resource-intensive and slow. Use only in development
    or with proper caching/rate limiting in production.
    """
    hx = (icao_hex or "").strip().lower()
    if not hx:
        print("ADS-B: No ICAO hex provided")
        return {}
    
    # Check cache
    cache_key = hx
    now = time.time()
    if cache_key in _cache:
        cached_data, cached_time = _cache[cache_key]
        if now - cached_time < _cache_timeout:
            print(f"ADS-B: Returning cached data for {hx}")
            return cached_data
    
    url = GLOBE_URL.format(hex=hx)
    driver = None
    
    try:
        print(f"ADS-B: Fetching data for {hx}...")
        options = _get_chrome_options()
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(timeout)
        
        driver.get(url)
        print(f"ADS-B: Page loaded, waiting for data...")
        
        # Wait for the ICAO hex to appear and match our query
        try:
            WebDriverWait(driver, timeout).until(
                lambda d: d.find_element(By.ID, "selected_icao").text.strip() != "" and
                          hx in d.find_element(By.ID, "selected_icao").text.strip().lower()
            )
            print(f"ADS-B: ICAO hex found, waiting for registration...")
        except Exception as e:
            print(f"ADS-B: Timeout waiting for ICAO hex: {e}")
            # Try to continue anyway - sometimes data is there but doesn't match
        
        # Wait for registration field to populate (indicates data is loaded)
        try:
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.ID, "selected_registration"))
            )
            print(f"ADS-B: Registration field found")
        except Exception as e:
            print(f"ADS-B: Timeout waiting for registration: {e}")
            # Continue anyway
        
        # Extra wait for dynamic content to fully populate
        time.sleep(2)
        
        soup = BeautifulSoup(driver.page_source, "html.parser")
        
        # Debug: Check if key elements exist
        icao_elem = soup.find(id="selected_icao")
        reg_elem = soup.find(id="selected_registration")
        print(f"ADS-B: Found selected_icao: {icao_elem is not None}, selected_registration: {reg_elem is not None}")
        
        result = {
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
            "position": _safe_text(soup, "selected_position"),
            "last_seen": _safe_text(soup, "selected_seen"),
            "last_pos_age": _safe_text(soup, "selected_seen_pos"),
            "source": _safe_text(soup, "selected_source"),
            "message_rate": _safe_text(soup, "selected_message_rate"),
            "pos_epoch": _safe_text(soup, "selected_pos_epoch"),
        }
        
        # Convert pos_epoch to int if possible
        if result.get("pos_epoch"):
            try:
                result["pos_epoch"] = int(result["pos_epoch"])
            except (ValueError, TypeError):
                pass
        
        # Check if we got any data
        non_empty = sum(1 for v in result.values() if v)
        print(f"ADS-B: Extracted {non_empty}/{len(result)} fields for {hx}")
        
        if non_empty == 0:
            print(f"ADS-B: No data found - aircraft may not be broadcasting")
            return {}
        
        # Cache result
        _cache[cache_key] = (result, now)
        return result
        
    except Exception as e:
        print(f"ADS-B scraping error for {hx}: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return {}
    finally:
        if driver:
            try:
                driver.quit()
            except Exception as e:
                print(f"ADS-B: Error closing driver: {e}")