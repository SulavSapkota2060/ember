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


# ---- stakes: who is in the building and what it is worth (CoStar) ----
SALE_PRICE_STATUS = {0: "Unknown", 1: "Affidavit", 2: "Approximate", 3: "Confirmed", 4: "Declaration",
                     5: "FullValue (from transfer tax, unconfirmed)", 6: "Mini", 7: "Unconfirmed", 8: "Allocated (portfolio share)"}

def _jsonl(name):
    with open(KIT_DIR / "data" / name) as f:
        for line in f:
            yield json.loads(line)

@lru_cache(1)
def _occupancies():
    by = {}
    for r in _jsonl("location-occupancy.jsonl"):
        by.setdefault(r["propertyId"], []).append(r)
    return by

@lru_cache(1)
def _companies():
    return {r["id"]: r for r in _jsonl("company-detail.jsonl")}

@lru_cache(1)
def _sales():
    by = {}
    for r in _jsonl("commercial-sale-comp.jsonl"):
        for e in r.get("entities") or []:
            if e.get("propertyId"):
                by.setdefault(e["propertyId"], []).append(r)
    return by

def get_stakes(building_id: int) -> dict:
    """Current tenants, employees, occupied area and last sale for a building. Every figure is as CoStar
    records it; missing values stay None (never estimated)."""
    pid = int(building_id)
    comps = _companies()
    tenants, area = [], 0.0
    for o in _occupancies().get(pid, []):
        if o.get("toDate"):
            continue  # null toDate = still in place
        c = comps.get(o.get("tenantLocationId")) or {}
        sf = sum(s.get("squareFeetOccupied") or 0 for s in o.get("spaces") or [])
        area += sf
        tenants.append({"name": c.get("name") or "Unknown tenant", "employees": c.get("numEmployees"),
                        "sqft": sf or None,
                        "lease_expires": ((o.get("leaseDeal") or {}).get("expirationDate") or "")[:10] or None})
    tenants.sort(key=lambda t: -(t["sqft"] or 0))
    known = [t["employees"] for t in tenants if t["employees"] is not None]
    sale = None
    cands = [r for r in _sales().get(pid, []) if r.get("soldDate")]
    if cands:
        r = max(cands, key=lambda r: r["soldDate"])
        multi = len(r.get("entities") or []) > 1
        sale = {"date": r["soldDate"][:10], "price": r.get("soldForPrice"),
                "price_status": SALE_PRICE_STATUS.get(r.get("salePriceStatus"), "Unknown"),
                "portfolio_sale": multi}
    return {"building_id": pid, "tenant_count": len(tenants), "tenants": tenants[:8],
            "occupied_sqft": area or None,
            "employees_known": sum(known) if known else None,
            "employees_known_for": f"{len(known)} of {len(tenants)} tenants",
            "last_sale": sale}
