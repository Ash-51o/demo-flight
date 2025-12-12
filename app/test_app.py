# test_adsb.py
from app.services.adsb_opensky import get_adsb_panel

# Test with a known active aircraft (NetJets)
test_hex = "a7da65"  # Example: NetJets aircraft
print(f"Testing with hex: {test_hex}")

result = get_adsb_panel(test_hex)
print(f"\nResult: {result}")