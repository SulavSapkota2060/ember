"""Equity lens: the neighborhood (census tract) pollution-and-vulnerability burden around a building.

SOURCE: OEHHA CalEnviroScreen 4.0, public ArcGIS feature service (point query by lat/lon), verified 2026-11-14 on four LA
buildings. `CIscoreP` is the tract's CalEnviroScreen percentile (higher = more burdened). Our readings: Barham Plaza 38.7,
7083 Hollywood Blvd 79.6, Westwood Center 38.1, The Preserve 91.5.
https://oehha.ca.gov/calenviroscreen/download-data

What it is NOT: a fire-risk measure (it describes pollution burden and population vulnerability), a statement about the
building's own tenants (it is a tract-level figure), or part of the exposure score (it is shown beside it as context).
CalEnviroScreen 5.0 exists as a draft; this is version 4.0, and tracts follow its census geography.
"""
from functools import lru_cache
import requests

URL = ("https://services1.arcgis.com/PCHfdHz4GlDNAhBb/arcgis/rest/services/CalEnviroScreen_4_0_Results_/FeatureServer/0/query")
FIELDS = "tract,CIscore,CIscoreP,CIdecile,PollutionP,PopCharP,povP"
SOURCE = "OEHHA CalEnviroScreen 4.0 (census tract)"
CAVEAT = ("Neighborhood context, not a fire metric: CalEnviroScreen measures pollution burden and population vulnerability "
          "for the census tract (version 4.0), not for this building's tenants. Not part of the exposure score.")


def ordinal(n) -> str:
    n = int(round(n))
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _parse(attrs: dict) -> dict:
    p = attrs.get("CIscoreP")
    if p is None:  # CalEnviroScreen leaves some tracts unscored: say so, never treat as zero
        return {"status": "no_score", "tract": str(attrs.get("tract")), "source": SOURCE}
    f = lambda k: None if attrs.get(k) is None else round(float(attrs[k]), 1)
    return {"status": "ok", "tract": str(int(attrs["tract"])) if attrs.get("tract") else None,
            "burden_percentile": round(float(p), 1), "burden_decile": attrs.get("CIdecile"),
            "pollution_percentile": f("PollutionP"), "vulnerability_percentile": f("PopCharP"),
            "poverty_percentile": f("povP"), "source": SOURCE}


@lru_cache(maxsize=2048)
def _lookup(lat: float, lon: float) -> dict:
    r = requests.get(URL, params={"f": "json", "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
                                  "spatialRel": "esriSpatialRelIntersects", "outFields": FIELDS, "returnGeometry": "false"},
                     timeout=30)
    r.raise_for_status()
    j = r.json()
    if "error" in j:
        raise RuntimeError(str(j["error"])[:150])
    feats = j.get("features") or []
    if not feats:
        return {"status": "no_tract", "source": SOURCE}
    return _parse(feats[0]["attributes"])


def lookup(lat: float, lon: float) -> dict:
    """Never raises. A failed lookup is status 'error' (shown as unavailable), never a made-up zero."""
    try:
        return _lookup(round(lat, 5), round(lon, 5))
    except Exception as e:
        return {"status": "error", "note": f"{type(e).__name__}: {str(e)[:120]}", "source": SOURCE}
