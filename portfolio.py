"""Portfolio view: every LA building that has a Matterport model, on two axes: SCREENING exposure vs embodied carbon
at stake. The screening score reuses scoring.py with only what the Vexcel screen saved (tree cover by zone, roof
material/discoloration, ground-level neighbor zones 1 and 4 only) plus CoStar sprinklers/construction and the CAL FIRE
zone. It has NO detections, roof-defect flags, vent counts, neighbor zones 2-3 or interior, so it will differ from the
full per-building score. It is for prioritisation only and is always labelled "screening"."""
import json
import scoring, sustainability
from config import OUT_DIR
from tools import costar

FILE = OUT_DIR / "portfolio_la.json"


def screening_facts(row: dict, prop: dict, zone: dict | None) -> dict:
    return {
        "z1t": row.get("z1t"), "z2t": row.get("z2t"), "z3t": row.get("z3t"),
        "z1b": row.get("z1b"), "z2b": None, "z3b": None, "z4b": row.get("z4b"),  # zones 2-3 not saved by the screen
        "roof_material": (row.get("roof") or "").lower() or None,
        "roof_missing": False, "roof_structural": False, "roof_tarp": False, "roof_patch": False, "roof_debris": False,
        "roof_discolor_pct": row.get("disc") or 0.0, "roof_vents": 0, "vents_screened": False,
        "deck": False, "dry_brush_n": 0, "wood_n": 0,
        "sprinklers": prop.get("sprinklers"), "construction": prop.get("construction"),
        "zone_hazard": zone, "interior": None, "has_structure_data": bool(row.get("structs")),
    }


def build(screen: dict, zones: dict, equity: dict | None = None) -> list[dict]:
    """screen: out/screen_la.json (id -> row); zones: id -> zone dict (hazard_zone format); equity: id -> equity.lookup dict.
    Skips rows without data."""
    rows = []
    for key, row in screen.items():
        if row.get("status") != 200 or row.get("z1t") is None:
            continue
        pid = int(row["id"])
        prop = costar.get_property(pid)
        stk = costar.get_stakes(pid)
        cs = sustainability.carbon_at_stake(prop.get("gba_sqft"))
        z = zones.get(str(pid))
        q = (equity or {}).get(str(pid)) or {}
        sc = scoring.score(screening_facts(row, prop, z if (z or {}).get("status") == "ok" else None))
        rows.append({"id": pid, "name": row.get("name") or prop.get("address"), "type": row.get("type"),
                     "lat": row["lat"], "lon": row["lon"], "screening_score": sc["score"], "band": sc["band"],
                     "zone": (z or {}).get("class") if (z or {}).get("in_zone") else ("none" if (z or {}).get("status") == "ok" else "unknown"),
                     "carbon_t": cs["tonnes_co2e"] if cs else None, "employees_known": stk["employees_known"],
                     "tenants": stk["tenant_count"], "sprinklers": prop.get("sprinklers"),
                     "ces_pctile": q.get("burden_percentile") if q.get("status") == "ok" else None,
                     "ces_decile": q.get("burden_decile") if q.get("status") == "ok" else None,
                     "ces_pollution": q.get("pollution_percentile") if q.get("status") == "ok" else None,
                     "ces_vulnerability": q.get("vulnerability_percentile") if q.get("status") == "ok" else None,
                     "ces_status": q.get("status", "not_run")})
    return rows


def load() -> list[dict] | None:
    return json.loads(FILE.read_text()) if FILE.exists() else None
