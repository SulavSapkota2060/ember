"""Transparent fire EXPOSURE heuristic (higher = more exposed, lower is better). An ESTIMATE, not a validated model.

Pure Python, no LLM, no network. The agent calls this; the UI toggles call it directly.
Score 0-100, higher = more exposed. Each factor is 0..1 and weighted. Factors with no data
are dropped and the remaining weights renormalized (and listed in `missing`).

Everything marked HEURISTIC below is my own starting guess and should be tuned on real buildings.
Vexcel `roof-characteristics.condition` is NOT used: its scale/direction is undocumented.
Zone distances are NOT stated anywhere in the data, so evidence strings never quote feet.
"""
from __future__ import annotations
import copy
import itertools

# HEURISTIC weights (sum 100 when everything is available).
WEIGHTS = {
    "vegetation": 25,     # tree cover in defensible-space zones around the building (Vexcel)
    "roof": 20,           # material + defects + ember-entry points (Vexcel)
    "surroundings": 10,   # neighboring buildings + combustible attachments (Vexcel)
    "protection": 15,     # sprinklers + construction material (CoStar)
    "occupancy": 5,       # tenant industry hazard (CoStar) - not wired yet
    "interior": 15,       # Matterport interior images read by a vision model (uncertain observations)
    "regional": 10,       # CAL FIRE Fire Hazard Severity Zone at the building (wildfire only)
}
LABELS = {
    "vegetation": "Vegetation near structure", "roof": "Roof", "surroundings": "Neighbors & attachments",
    "protection": "Building protection", "occupancy": "Occupancy hazard",
    "interior": "Interior protection", "regional": "Regional hazard zone",
}
VEG_SATURATION_PCT = 60.0      # HEURISTIC: weighted zone tree cover at/above this = factor 1.0
NEIGHBOR_SATURATION_PCT = 40.0  # HEURISTIC

ROOF_MATERIAL_RISK = {  # HEURISTIC: combustibility of the roof covering
    "wood": 1.0, "shake": 1.0, "shingle": 0.5, "gravel": 0.3,
    "membrane": 0.2, "tile": 0.15, "metal": 0.15, "concrete": 0.1,
}
CONSTRUCTION_RISK = {  # CoStar BuildingConstructionMaterialType (schemas/enums.json); HEURISTIC values
    1: 0.25,  # Masonry
    2: 0.3,   # Metal
    3: 0.15,  # ReinforcedConcrete
    4: 0.2,   # Steel
    5: 0.8,   # WoodFrame
    6: 0.25,  # Brownstone
    7: 0.2,   # Limestone
}
BANDS = [(33, "low"), (66, "moderate"), (101, "high")]


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def build_facts(prop: dict, attrs: dict, dets: list | None = None, zone=None, interior=None) -> dict:
    """Flatten CoStar record + Vexcel attributes (+ optional detections) into the facts the scorer reads."""
    structs = attrs.get("structures") or []
    s = next((x for x in structs if x.get("primary-structure")), structs[0] if structs else {})
    g = s.get
    dets = dets or []
    n = lambda label: sum(1 for d in dets if d.get("label") == label)
    return {
        "z1t": g("defensible-space.zone1-tree"), "z2t": g("defensible-space.zone2-tree"),
        "z3t": g("defensible-space.zone3-tree"),
        "z1b": g("defensible-space.zone1-building"), "z2b": g("defensible-space.zone2-building"),
        "z3b": g("defensible-space.zone3-building"), "z4b": g("defensible-space.zone4-building"),
        "roof_material": (g("roof-characteristics.material.class") or "").lower() or None,
        "roof_missing": bool(g("roof-defects.missing-material.detected")),
        "roof_structural": bool(g("roof-defects.structural-damage.detected")),
        "roof_tarp": bool(g("roof-defects.tarp.detected")),
        "roof_patch": bool(g("roof-defects.patch.detected")),
        "roof_debris": bool(g("roof-defects.debris.detected")),
        "roof_discolor_pct": _num(g("roof-defects.discoloration.percent")) or 0.0,
        "roof_vents": _num(g("roof-objects.roof-vent.count")) or 0, "vents_screened": False,
        "deck": bool(attrs.get("deck.detected")),
        "dry_brush_n": n("dry brush"), "wood_n": n("wood deck") + n("wood fence"),
        "sprinklers": prop.get("sprinklers"), "construction": prop.get("construction"),
        "zone_hazard": zone, "interior": interior,
        "has_structure_data": bool(s),
    }


def _vegetation(f):
    z = [f.get("z1t"), f.get("z2t"), f.get("z3t")]
    if any(v is None for v in z):
        return None, [], "no defensible-space tree data"
    w = (3 * z[0] + 2 * z[1] + z[2]) / 6.0
    val = min(1.0, w / VEG_SATURATION_PCT)
    ev = [f"tree cover by zone (1 nearest the building): zone 1 {z[0]:g}%, zone 2 {z[1]:g}%, zone 3 {z[2]:g}%"]
    if f.get("dry_brush_n"):
        bump = min(0.15, 0.05 * f["dry_brush_n"])
        val = min(1.0, val + bump)
        ev.append(f"{f['dry_brush_n']} 'dry brush' detection(s) (+{bump:.2f})")
    return val, ev, None


def _roof(f):
    if not f.get("has_structure_data"):
        return None, [], "no roof data"
    ev = []
    mat = f.get("roof_material")
    if mat in ROOF_MATERIAL_RISK:
        m = ROOF_MATERIAL_RISK[mat]
        ev.append(f"roof material: {mat}")
    elif mat:
        m = 0.4
        ev.append(f"roof material '{mat}' not in table, neutral value used")
    else:
        m = 0.4
        ev.append("roof material unknown, neutral value used")
    d = (0.3 * f["roof_missing"] + 0.3 * f["roof_structural"] + 0.15 * f["roof_tarp"]
         + 0.1 * f["roof_patch"] + 0.2 * f["roof_debris"] + min(0.2, f["roof_discolor_pct"] / 100.0))
    d = min(1.0, d)
    flags = [k for k, v in (("missing material", f["roof_missing"]), ("structural damage", f["roof_structural"]),
                            ("tarp", f["roof_tarp"]), ("patch", f["roof_patch"]), ("debris", f["roof_debris"])) if v]
    ev.append("roof defects: " + (", ".join(flags) if flags else "none flagged")
              + f"; discoloration {f['roof_discolor_pct']:g}% of roof")
    val = 0.6 * m + 0.4 * d
    vents = 0 if f.get("vents_screened") else f.get("roof_vents", 0)
    if vents:
        bump = min(0.2, 0.02 * vents)
        val = min(1.0, val + bump)
        ev.append(f"{vents} roof vent(s) unscreened (ember entry, +{bump:.2f})")
    return val, ev, None


def _surroundings(f):
    b = [f.get("z1b"), f.get("z2b"), f.get("z3b"), f.get("z4b")]
    if any(v is None for v in b):
        return None, [], "no neighbor-building data"
    avg = (3 * b[0] + 2 * b[1] + b[2] + b[3]) / 7.0
    nb = min(1.0, avg / NEIGHBOR_SATURATION_PCT)
    att = min(1.0, 0.5 * f.get("deck", False) + min(0.5, 0.15 * f.get("wood_n", 0)))
    ev = [f"neighboring-building cover by zone: {b[0]:g}% / {b[1]:g}% / {b[2]:g}% / {b[3]:g}% (zones 1-4)"]
    if f.get("deck"):
        ev.append("deck detected")
    if f.get("wood_n"):
        ev.append(f"{f['wood_n']} wood deck/fence detection(s)")
    return 0.6 * nb + 0.4 * att, ev, None


def _protection(f):
    parts, ev = [], []
    sp = f.get("sprinklers")
    if sp is not None:
        parts.append((0.6, 0.1 if sp else 0.9))
        ev.append("sprinklers: " + ("yes" if sp else "no") + " (CoStar)")
    c = f.get("construction")
    if c in CONSTRUCTION_RISK:
        parts.append((0.4, CONSTRUCTION_RISK[c]))
        ev.append(f"construction material code {c} (CoStar)")
    if not parts:
        return None, [], "no sprinkler/construction data"
    tw = sum(w for w, _ in parts)
    return sum(w * v for w, v in parts) / tw, ev, None


# HEURISTIC interior model. facts["interior"] = {"n_images": N, "obs": {item: seen|not_seen|cannot_tell}}.
# Start at 0.5 (unknown). Protective items SEEN lower risk; hazards SEEN raise it. An item not seen is NOT
# treated as absent (images cover little of the building), so it adds no penalty.
INTERIOR_BASE = 0.5
# sprinkler_head is deliberately NOT scored here: the vision read confuses it with HVAC diffusers (seen on
# 7083 Hollywood Blvd), and sprinklers are already scored from CoStar in the protection factor.
INTERIOR_PROTECT = {"fire_extinguisher": ("fire extinguisher", 0.15),
                    "alarm_or_smoke_detector": ("alarm/detector", 0.10), "exit_sign": ("exit sign", 0.05)}
INTERIOR_HAZARD = {"blocked_egress": ("blocked exit path", 0.25), "stored_combustibles": ("stored combustibles", 0.15)}


def _interior(f):
    i = f.get("interior")
    if not i or not i.get("n_images"):
        return None, [], "no interior images analyzed"
    obs, n = i.get("obs") or {}, i["n_images"]
    v, ev = INTERIOR_BASE, []
    for key, (label, d) in INTERIOR_PROTECT.items():
        st = obs.get(key)
        if st == "seen":
            v -= d; ev.append(f"{label}: seen")
        else:
            ev.append(f"{label}: " + (f"not seen in {n} image(s), not proof of absence" if st == "not_seen" else "can't tell"))
    for key, (label, d) in INTERIOR_HAZARD.items():
        if obs.get(key) == "seen":
            v += d; ev.append(f"{label}: seen")
    ev.append(f"based on {n} Matterport image(s) read by a vision model (uncertain)")
    return min(max(v, 0.0), 1.0), ev, None


# HEURISTIC: CAL FIRE class -> 0..1. Outside every mapped zone gets 0.1, not 0: the zone maps in the public
# service are older than CAL FIRE's newer local-area recommendations, so "outside" is not proof of low hazard.
ZONE_RISK = {"Very High": 1.0, "High": 0.75, "Moderate": 0.5}


def _regional(f):
    z = f.get("zone_hazard")
    if not z or z.get("status") != "ok":
        return None, [], "hazard zone lookup unavailable"
    if not z.get("in_zone"):
        return 0.1, ["outside CAL FIRE mapped fire hazard zones (older maps; not proof of low hazard)"], None
    return ZONE_RISK.get(z["class"], 0.5), [f"CAL FIRE Fire Hazard Severity Zone: {z['class']} ({z['area']})"], None


def _optional(fact_key, label_key):
    def fn(f):
        v = f.get(fact_key)
        name = LABELS[label_key].lower()
        if v is None:
            return None, [], f"{name} not available"
        return float(v), [f"{name}: {v}"], None
    return fn


_FACTORS = {
    "vegetation": _vegetation, "roof": _roof, "surroundings": _surroundings, "protection": _protection,
    "occupancy": _optional("occupancy", "occupancy"), "interior": _interior,
    "regional": _regional,
}


def score(facts: dict) -> dict:
    """Return {score, band, factors[], missing[]}. Weights renormalize over available factors."""
    got, missing = {}, []
    for key, fn in _FACTORS.items():
        val, ev, why = fn(facts)
        if val is None:
            missing.append({"factor": key, "reason": why})
        else:
            got[key] = (val, ev)
    total_w = sum(WEIGHTS[k] for k in got)
    factors, total = [], 0.0
    for key, (val, ev) in got.items():
        w = 100.0 * WEIGHTS[key] / total_w if total_w else 0.0
        pts = val * w
        total += pts
        factors.append({"key": key, "label": LABELS[key], "value": round(val, 3), "weight": round(w, 1),
                        "points": round(pts, 1), "evidence": ev})
    factors.sort(key=lambda x: -x["points"])
    band = next(name for lim, name in BANDS if total < lim)
    return {"score": round(total), "band": band, "factors": factors, "missing": missing,
            "note": "heuristic ESTIMATE, not a validated model"}


# ---- fixes / what-if ----
def _f_clear_zone1(f): f["z1t"] = 0 if f.get("z1t") is not None else None
def _f_thin_zone2(f): f["z2t"] = f["z2t"] * 0.5 if f.get("z2t") is not None else None
def _f_vents(f): f["vents_screened"] = True
def _f_roof(f): f.update(roof_material="tile", roof_missing=False, roof_structural=False, roof_tarp=False,
                          roof_patch=False, roof_debris=False, roof_discolor_pct=0.0)
def _f_debris(f): f["roof_debris"] = False
def _f_wood(f): f.update(deck=False, wood_n=0)
def _f_sprinklers(f):
    if f.get("sprinklers") is False:  # unknown (None) stays unknown: no fix is claimed
        f["sprinklers"] = True
def _f_brush(f): f["dry_brush_n"] = 0
def _set_obs(f, key, frm, to):
    o = (f.get("interior") or {}).get("obs")
    if o is not None and o.get(key) == frm:
        o[key] = to
def _f_egress(f): _set_obs(f, "blocked_egress", "seen", "not_seen")
def _f_stored(f): _set_obs(f, "stored_combustibles", "seen", "not_seen")
def _f_extinguishers(f): _set_obs(f, "fire_extinguisher", "not_seen", "seen")

# cost is RELATIVE (low/med/high). No dollar figures: the user supplies those.
FIXES = {
    "clear_zone1": ("Clear trees/brush nearest the building (zone 1)", "med", _f_clear_zone1),
    "thin_zone2": ("Thin vegetation in zone 2 by about half", "med", _f_thin_zone2),
    "screen_vents": ("Fit ember-resistant mesh on roof vents", "low", _f_vents),
    "replace_roof": ("Replace roof with a non-combustible covering and repair defects", "high", _f_roof),
    "clear_roof_debris": ("Clear debris from roof and gutters", "low", _f_debris),
    "replace_wood": ("Replace wood decks/fences near the building", "med", _f_wood),
    "add_sprinklers": ("Add or restore sprinkler coverage", "high", _f_sprinklers),
    "clear_dry_brush": ("Remove detected dry brush", "low", _f_brush),
    "clear_egress": ("Clear blocked exit paths (seen in Matterport images)", "low", _f_egress),
    "clear_stored_combustibles": ("Remove stored combustibles (seen in Matterport images)", "low", _f_stored),
    "add_extinguishers": ("Place/verify fire extinguishers (none seen in Matterport images)", "low", _f_extinguishers),
}
_COST_RANK = {"low": 1, "med": 2, "high": 3}


def apply_fixes(facts: dict, fix_ids) -> dict:
    out = copy.deepcopy(facts)
    for fid in fix_ids:
        FIXES[fid][2](out)
    return out


def whatif(facts: dict, fix_ids) -> dict:
    """Score before/after with the delta and which factors moved."""
    before, after = score(facts), score(apply_fixes(facts, fix_ids))
    bf = {f["key"]: f["points"] for f in before["factors"]}
    moved = [{"factor": f["key"], "label": f["label"], "from": bf.get(f["key"]), "to": f["points"]}
             for f in after["factors"] if bf.get(f["key"]) != f["points"]]
    return {"before": before["score"], "after": after["score"], "delta": before["score"] - after["score"],
            "factors_moved": moved}


def rank_fixes(facts: dict) -> list[dict]:
    """Single-fix deltas, only fixes that change something, best delta per relative cost first."""
    base = score(facts)["score"]
    rows = []
    for fid, (label, cost, _fn) in FIXES.items():
        d = base - score(apply_fixes(facts, [fid]))["score"]
        if d > 0:
            rows.append({"id": fid, "label": label, "cost": cost, "points_removed": d})
    rows.sort(key=lambda r: -(r["points_removed"] / _COST_RANK[r["cost"]]))
    return rows


def plan_within_budget(facts: dict, budget: float, cost_usd: dict) -> dict:
    """Best set of fixes within `budget`. `cost_usd` maps fix id -> dollars, supplied by the user
    (we never invent prices). Exhaustive over useful fixes with a known cost (small set)."""
    base = score(facts)["score"]
    cand = [r["id"] for r in rank_fixes(facts) if r["id"] in cost_usd]
    best = {"fixes": [], "cost": 0.0, "after": base}
    for k in range(1, len(cand) + 1):
        for combo in itertools.combinations(cand, k):
            c = sum(cost_usd[i] for i in combo)
            if c > budget:
                continue
            after = score(apply_fixes(facts, combo))["score"]
            if after < best["after"] or (after == best["after"] and c < best["cost"] and combo):
                best = {"fixes": list(combo), "cost": c, "after": after}
    best["before"] = base
    best["skipped_no_cost"] = [r["id"] for r in rank_fixes(facts) if r["id"] not in cost_usd]
    return best
