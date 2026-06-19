"""Anchorage bounding boxes and chokepoint axis config for analytics detection.

Each anchorage zone is ((lat_min, lon_min), (lat_max, lon_max)).
Each chokepoint entry: (axis, positive_label, negative_label).
  axis: 'lat' or 'lon' - dimension along which net displacement is measured.
  positive/negative: direction labels for displacement sign.
"""

from __future__ import annotations

from ais.regions import REGIONS  # re-exported for use by detect.py

# Curated anchorage zones inside covered regions (verified against region bboxes in regions.py)
ANCHORAGE_ZONES: dict[str, tuple[tuple[float, float], tuple[float, float]]] = {
    "fujairah": ((24.9, 56.3), (25.4, 56.85)),         # UAE east coast; hormuz region
    "singapore_west": ((1.1, 103.5), (1.35, 103.85)),  # Singapore West Anchorage
    "singapore_east": ((1.1, 104.0), (1.35, 104.5)),   # Singapore East / Johor Strait
    "suez_roads": ((29.7, 32.3), (30.5, 32.7)),        # Suez waiting anchorage / Great Bitter Lake
    "port_said": ((31.2, 32.2), (31.65, 32.55)),       # Port Said / northern Suez entrance
    # ARA (Amsterdam-Rotterdam-Antwerp) - extended to cover full port complex
    "rotterdam": ((51.7, 3.7), (52.1, 4.3)),           # Rotterdam / Europoort / Hook of Holland
    "antwerp": ((51.1, 3.9), (51.5, 4.5)),             # Antwerp port
    "amsterdam": ((52.25, 4.6), (52.6, 5.05)),         # Amsterdam / IJmuiden / North Sea Canal
    "terneuzen": ((51.2, 3.7), (51.5, 3.95)),          # Terneuzen / Flushing / Ghent canal
    "zeebrugge": ((51.15, 3.0), (51.4, 3.4)),          # Zeebrugge / Bruges
    "qingdao": ((35.8, 119.85), (36.25, 120.45)),      # Qingdao / Rizhao N China
    "port_hedland": ((-20.45, 118.35), (-20.1, 118.8)),# Port Hedland W Australia
    "richards_bay": ((-28.95, 32.0), (-28.65, 32.15)), # Richards Bay South Africa
    "santos": ((-24.15, -46.55), (-23.75, -46.1)),     # Santos / Paranagua Brazil
    "galveston_ltg": ((28.85, -94.2), (29.35, -93.45)),# Galveston lightering area, US Gulf
    "arab_gulf_north": ((28.5, 48.5), (30.5, 50.6)),   # Kuwait/Basra N Gulf anchorage
    "tubarao": ((-20.4, -40.5), (-19.75, -40.0)),      # Vitoria/Tubarao Brazil
    "bab_djibouti": ((11.5, 42.0), (12.5, 43.5)),      # Djibouti anchorage area
    # Japan / Korea major port areas (japan_korea region bbox: lat 33-38.5, lon 126-142)
    "busan": ((34.9, 128.8), (35.2, 129.2)),           # Busan port complex
    "ulsan": ((35.4, 129.2), (35.6, 129.6)),           # Ulsan petrochemical terminal
    "gwangyang": ((34.8, 127.6), (35.05, 127.95)),     # Gwangyang steel / bulk
    "yeosu": ((34.55, 127.4), (34.85, 127.85)),        # Yeosu / Yosu oil terminal
    "incheon": ((37.3, 126.3), (37.65, 126.75)),       # Incheon
    "tokyo_bay": ((35.25, 139.55), (35.7, 140.0)),     # Tokyo / Yokohama / Kawasaki
    "chiba": ((35.4, 139.85), (35.65, 140.1)),         # Chiba / Sodegaura
    "nagoya": ((34.7, 136.65), (35.1, 137.1)),         # Nagoya / Yokkaichi
    "osaka_kobe": ((34.45, 134.9), (34.75, 135.55)),   # Osaka / Kobe
    "mizushima": ((34.35, 133.55), (34.6, 133.95)),    # Mizushima / Fukuyama
}

# 9 chokepoints: (dominant_axis, positive_direction_label, negative_direction_label)
# Displacement is last_lat - first_lat (or lon). Sign determines direction.
CHOKEPOINT_AXES: dict[str, tuple[str, str, str]] = {
    "singapore_malacca": ("lon", "eastbound", "westbound"),   # IO -> SCS = east
    "suez": ("lat", "northbound", "southbound"),              # Red Sea -> Med = north
    "hormuz": ("lon", "outbound", "inbound_gulf"),            # Leaving Gulf = east
    "panama": ("lon", "eastbound", "westbound"),              # Pacific -> Atlantic = east
    "gibraltar": ("lon", "eastbound", "westbound"),           # Atlantic -> Med = east
    "bosphorus_dardanelles": ("lat", "northbound", "southbound"),  # Med -> Black Sea = north
    "dover_channel": ("lon", "eastbound", "westbound"),       # Atlantic -> North Sea = east
    "cape_good_hope": ("lon", "eastbound", "westbound"),      # Atlantic -> Indian = east
    "bab_el_mandeb": ("lat", "northbound", "southbound"),     # Gulf of Aden -> Red Sea = north
}

# Segment design draughts (metres) for laden/ballast fallback when history is shallow
DESIGN_DRAUGHT: dict[str, float] = {
    "ULCC": 24.0,
    "VLCC": 22.0,
    "Suezmax": 17.0,
    "Aframax": 14.9,
    "Panamax": 13.5,   # tanker
    "Capesize": 18.2,
    "Supramax": 12.8,
    "Handymax": 11.5,
    "Handysize": 10.0,
}

CHOKEPOINTS = list(CHOKEPOINT_AXES.keys())
