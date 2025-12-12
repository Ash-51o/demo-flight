# app/services/adsb_opensky.py
"""
OpenSky Network API - Free alternative to ADS-B Exchange scraping
No Selenium required, < 1 second response time
API Docs: https://openskynetwork.github.io/opensky-api/
"""

import requests
from typing import Optional, Dict
import time

OPENSKY_API = "https://opensky-network.org/api/states/all"

# Cache results for 30 seconds
_cache = {}
_cache_timeout = 30

def get_adsb_data(icao_hex: str, timeout: int = 5) -> Dict:
    """
    Get live ADS-B data from OpenSky Network API.
    
    Args:
        icao_hex: ICAO 24-bit address (Mode S code) in hex format
        timeout: Request timeout in seconds
        
    Returns:
        Dict with aircraft position and status data
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
            print(f"[OpenSky] Returning cached data for {hx}")
            return cached_data
    
    print(f"[OpenSky] Fetching data for ICAO hex: {hx}")
    
    try:
        # OpenSky API request
        response = requests.get(
            OPENSKY_API,
            params={"icao24": hx},
            timeout=timeout
        )
        
        if not response.ok:
            print(f"[OpenSky] HTTP {response.status_code}: {response.text}")
            return {}
        
        data = response.json()
        states = data.get("states", [])
        
        if not states or len(states) == 0:
            print(f"[OpenSky] No data found - aircraft not currently transmitting")
            return {}
        
        # OpenSky returns array of values for each aircraft
        # Format: [icao24, callsign, origin_country, time_position, last_contact,
        #          longitude, latitude, baro_altitude, on_ground, velocity,
        #          true_track, vertical_rate, sensors, geo_altitude, squawk,
        #          spi, position_source]
        state = states[0]
        
        # Parse the state vector
        result = {
            "hex": state[0],  # icao24
            "callsign": state[1].strip() if state[1] else None,
            "country": state[2],
            "last_contact": int(state[4]) if state[4] else None,
            "longitude": state[5],
            "latitude": state[6],
            "baro_altitude": f"{state[7]:.0f} m" if state[7] is not None else None,
            "on_ground": state[8],
            "velocity": f"{state[9]:.1f} m/s" if state[9] is not None else None,
            "groundspeed_kt": f"{state[9] * 1.94384:.0f}" if state[9] is not None else None,
            "true_track": f"{state[10]:.1f}°" if state[10] is not None else None,
            "vertical_rate": f"{state[11]:.1f} m/s" if state[11] is not None else None,
            "geo_altitude": f"{state[13]:.0f} m" if state[13] is not None else None,
            "squawk": state[14],
            "position": f"{state[6]:.4f}°, {state[5]:.4f}°" if state[6] and state[5] else None,
            "last_seen": _format_time_ago(state[4]) if state[4] else None,
            "pos_epoch": int(state[3]) if state[3] else None,
        }
        
        # Cache the result
        _cache[cache_key] = (result, now)
        
        print(f"[OpenSky] ✓ Data retrieved successfully")
        return result
        
    except requests.Timeout:
        print(f"[OpenSky] Request timed out after {timeout}s")
        return {}
    except requests.RequestException as e:
        print(f"[OpenSky] Request failed: {e}")
        return {}
    except Exception as e:
        print(f"[OpenSky] Error: {e}")
        return {}


def _format_time_ago(timestamp: float) -> str:
    """Format Unix timestamp as 'X time ago'"""
    try:
        now = time.time()
        diff = int(now - timestamp)
        
        if diff < 60:
            return f"{diff}s ago"
        elif diff < 3600:
            return f"{diff // 60}m ago"
        elif diff < 86400:
            return f"{diff // 3600}h ago"
        else:
            return f"{diff // 86400}d ago"
    except:
        return None


# For backward compatibility, alias the function
get_adsb_panel = get_adsb_data

# URL template for linking to flight tracking sites
GLOBE_URL = "https://globe.adsbexchange.com/?icao={hex}"
OPENSKY_URL = "https://opensky-network.org/network/explorer?icao24={hex}"