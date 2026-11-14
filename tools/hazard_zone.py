"""Official wildfire hazard zone lookup: CAL FIRE Fire Hazard Severity Zones (public ArcGIS MapServer).
Verified 2026-11-14: point query works; hillside points (Bel Air, Pacific Palisades, Topanga) return 'Very High'.
Layers used: 0 = State Responsibility Areas (2007 map), 1 = Local Responsibility Areas (2011 map). This service's
LRA map is OLDER than CAL FIRE's newer LRA recommendations, so 'outside mapped zones' is not proof of low hazard.
Flood (FEMA) is not implemented."""
from functools import lru_cache
import requests

BASE = "https://services.gis.ca.gov/arcgis/rest/services/Environment/Fire_Severity_Zones/MapServer"
LAYERS = {0: "SRA (2007 map)", 1: "LRA (2011 map)"}
ORDER = {"Moderate": 1, "High": 2, "Very High": 3}


@lru_cache(maxsize=512)
def _lookup(lat: float, lon: float) -> dict:
    hits = []
    for layer, area in LAYERS.items():
        r = requests.get(f"{BASE}/{layer}/query", params={
            "f": "json", "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "outFields": "HAZ_CLASS", "returnGeometry": "false"}, timeout=30)
        r.raise_for_status()
        j = r.json()
        if "error" in j:
            raise RuntimeError(str(j["error"])[:150])
        hits += [(f["attributes"].get("HAZ_CLASS"), area) for f in j.get("features", [])]
    src = "CAL FIRE Fire Hazard Severity Zones"
    if not hits:
        return {"status": "ok", "in_zone": False, "class": None, "area": None, "source": src,
                "note": "outside the mapped zones in this (older) CAL FIRE service; not proof of low hazard"}
    cls, area = max(hits, key=lambda h: ORDER.get(h[0], 0))
    return {"status": "ok", "in_zone": True, "class": cls, "area": area, "source": src}


def get_hazard_zone(lat: float, lon: float, hazard: str) -> dict:
    if hazard != "wildfire":
        return {"status": "unavailable", "note": f"no official zone lookup implemented for {hazard}"}
    try:
        return _lookup(round(lat, 5), round(lon, 5))
    except Exception as e:
        return {"status": "error", "note": f"zone lookup failed: {type(e).__name__}: {str(e)[:120]}"}
