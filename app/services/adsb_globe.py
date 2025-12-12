# app/services/adsb_globe.py
import os
import functools
from typing import Optional, Dict
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager
import time

GLOBE_URL = "https://globe.adsbexchange.com/?icao={hex}"

# Cache results for 30 seconds to avoid repeated scraping
_cache = {}
_cache_timeout = 30

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
    options.add_argument('--disable-dev-shm-usage')
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
    el = soup.find(['span', 'div'], id=el_id)
    txt = el.get_text(strip=True) if el else None
    return txt or None

def get_adsb_panel(icao_hex: str, timeout: int = 15) -> Dict:
    """
    Returns a dict of key fields from ADS-B Exchange panel.
    
    WARNING: This is resource-ixqxntensive and slow. Use only in development
    or with proper caching/rate limiting in production.
    """
    hx = (icao_hex or "").strip().lower()
    if not hx:
        return {}
    
    # Check cache
    cache_key = hx
    now = time.time()
    if cache_key in _cache:
        cached_data, cached_time = _cache[cache_key]
        if now - cached_time < _cache_timeout:
            return cached_data
    
    url = GLOBE_URL.format(hex=hx)
    driver = None
    
    try:
        options = _get_chrome_options()
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(timeout)
        
        driver.get(url)
        
        # Wait for data
        WebDriverWait(driver, timeout).until(
            lambda d: d.find_element(By.ID, "selected_icao").text.strip() != ""
        )
        
        soup = BeautifulSoup(driver.page_source, "html.parser")
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
        
        # Cache result
        _cache[cache_key] = (result, now)
        return result
        
    except Exception as e:
        print(f"ADS-B scraping error for {hx}: {e}")
        return {}
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass