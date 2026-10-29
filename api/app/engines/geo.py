"""Geo helpers. PostGIS does the heavy lifting; these are the pure-Python bits."""
from __future__ import annotations

import math

# Single serviced region for the demo (loosely Bengaluru). Reports outside it
# are geographically implausible.
SERVICE_BBOX = (12.80, 77.40, 13.18, 77.80)  # min_lat, min_lng, max_lat, max_lng

WARDS: dict[str, tuple[float, float, float, float]] = {
    "WARD-NORTH": (13.02, 77.55, 13.18, 77.70),
    "WARD-CENTRAL": (12.94, 77.55, 13.02, 77.65),
    "WARD-SOUTH": (12.80, 77.55, 12.94, 77.70),
    "WARD-EAST": (12.90, 77.70, 13.18, 77.80),
    "WARD-WEST": (12.80, 77.40, 13.18, 77.55),
}


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def in_service_area(lat: float, lng: float) -> bool:
    min_lat, min_lng, max_lat, max_lng = SERVICE_BBOX
    return min_lat <= lat <= max_lat and min_lng <= lng <= max_lng


def ward_for(lat: float, lng: float) -> str:
    for name, (min_lat, min_lng, max_lat, max_lng) in WARDS.items():
        if min_lat <= lat <= max_lat and min_lng <= lng <= max_lng:
            return name
    return "WARD-UNASSIGNED"


def wkt_point(lat: float, lng: float) -> str:
    return f"SRID=4326;POINT({lng} {lat})"
