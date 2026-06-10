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
    "rotterdam": ((51.85, 3.9), (52.05, 4.2)),         # Rotterdam / Europoort
    "qingdao": ((35.8, 119.85), (36.25, 120.45)),      # Qingdao / Rizhao N China
    "port_hedland": ((-20.45, 118.35), (-20.1, 118.8)),# Port Hedland W Australia
    "richards_bay": ((-28.95, 32.0), (-28.65, 32.15)), # Richards Bay South Africa
    "santos": ((-24.15, -46.55), (-23.75, -46.1)),     # Santos / Paranagua Brazil
    "galveston_ltg": ((28.85, -94.2), (29.35, -93.45)),# Galveston lightering area, US Gulf
    "arab_gulf_north": ((28.5, 48.5), (30.5, 50.6)),   # Kuwait/Basra N Gulf anchorage
    "tubarao": ((-20.4, -40.5), (-19.75, -40.0)),      # Vitoria/Tubarao Brazil
    "bab_djibouti": ((11.5, 42.0), (12.5, 43.5)),      # Djibouti anchorage area
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
