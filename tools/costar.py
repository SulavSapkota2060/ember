"""CoStar kit access. Reads the local kit (no key needed)."""
import csv, json
from functools import lru_cache
from config import KIT_DIR

PROPERTY_TYPES = {1: "Hospitality", 2: "Industrial", 5: "Office", 6: "GeneralRetail", 7: "Flex",
                  10: "Specialty", 11: "Multifamily"}  # subset of schemas/enums.json PropertyType

@lru_cache(1)
def _props():
    out = {}
    with open(KIT_DIR / "data/commercial-property.jsonl") as f:
        for line in f:
            r = json.loads(line)
            out[r["id"]] = r
    return out

@lru_cache(1)
def _models():
    m = {}
    with open(KIT_DIR / "commercial-properties.csv") as f:
        for x in csv.DictReader(f):
            if x["MatterportModelId"]:
                m.setdefault(int(x["PropertyId"]), []).append(x["MatterportModelId"])
    return m

def list_buildings(city=None, include_multifamily=False, limit=500):
    rows = []
    for pid, r in _props().items():
        a = r.get("primaryAddress") or {}
        if not a.get("latitude"):
            continue
        if r["propertyType"] == 11 and not include_multifamily:
            continue
        c = (a.get("city") or "").title()
        if city and c != city:
            continue
        rows.append({"id": pid, "name": r.get("buildingName") or f"{a.get('streetNumber','')} {a.get('streetName','')} {a.get('streetType','')}".strip(),
                     "city": c, "type": PROPERTY_TYPES.get(r["propertyType"], str(r["propertyType"])),
                     "lat": a["latitude"], "lon": a["longitude"], "matterport": pid in _models()})
    return rows[:limit]

def get_property(building_id: int) -> dict:
    r = _props().get(int(building_id))
    if not r:
        return {"error": f"unknown building_id {building_id}"}
    a = r["primaryAddress"]
    return {"id": r["id"], "name": r.get("buildingName"), "address": f"{a.get('streetNumber','')} {a.get('streetName','')} {a.get('streetType','')}, {a.get('city')}, {a.get('state')}",
            "lat": a["latitude"], "lon": a["longitude"],
            "type": PROPERTY_TYPES.get(r["propertyType"], r["propertyType"]),
            "buildingClass": r.get("buildingClass"), "stories": r.get("numStoriesAboveGrade"),
            "gba_sqft": r.get("grossBuildingAreaSquareFeet"), "rba_sqft": r.get("rentableBuildingAreaSquareFeet"),
            "lotSize": r.get("lotSize"), "sprinklers": r.get("hasSprinklers"),
            "construction": r.get("buildingConstructionMaterial"),
            "matterport_models": _models().get(r["id"], [])}
