"""Run: .venv/bin/python -I tests/test_scoring.py   (plain asserts, no network)"""
import sys; sys.path.insert(0, ".")
import scoring as sc

def base(**kw):
    f = dict(z1t=10, z2t=10, z3t=10, z1b=5, z2b=5, z3b=5, z4b=5, roof_material="membrane", roof_missing=False,
             roof_structural=False, roof_tarp=False, roof_patch=False, roof_debris=False, roof_discolor_pct=0.0,
             roof_vents=0, vents_screened=False, deck=False, dry_brush_n=0, wood_n=0, sprinklers=True,
             construction=3, zone_hazard=None, interior=None, has_structure_data=True)
    f.update(kw); return f

# 1. bounds + determinism
lo = sc.score(base(z1t=0, z2t=0, z3t=0, z1b=0, z2b=0, z3b=0, z4b=0, roof_material="metal"))
hi = sc.score(base(z1t=90, z2t=90, z3t=90, z1b=60, z2b=60, z3b=60, z4b=60, roof_material="wood", roof_missing=True,
                   roof_debris=True, roof_vents=20, deck=True, wood_n=5, sprinklers=False, construction=5, dry_brush_n=5))
assert 0 <= lo["score"] < 33 and hi["score"] >= 66, (lo["score"], hi["score"])
assert sc.score(base()) == sc.score(base())

# 2. monotonic in vegetation
s = [sc.score(base(z1t=v, z2t=v, z3t=v))["score"] for v in (0, 20, 40, 60)]
assert s == sorted(s) and s[0] < s[-1], s

# 3. missing data renormalizes, never crashes, is reported
f = base(sprinklers=None, construction=None, z1t=None)
r = sc.score(f)
assert {m["factor"] for m in r["missing"]} >= {"vegetation", "protection", "occupancy", "interior", "regional"}  # interior None here
assert 0 <= r["score"] <= 100
assert abs(sum(x["weight"] for x in r["factors"]) - 100) < 0.5
assert sc.score({"has_structure_data": False})["score"] == 0  # no data -> empty, not an exception

# 4. fixes only ever help, and don't mutate input
f = base(z1t=60, z2t=50, z3t=40, roof_vents=10, roof_debris=True, deck=True, sprinklers=False, dry_brush_n=2, roof_material="shingle")
snap = dict(f)
for fid in sc.FIXES:
    assert sc.whatif(f, [fid])["delta"] >= 0, fid
assert f == snap
assert sc.whatif(f, list(sc.FIXES))["after"] < sc.score(f)["score"]

# 5. rank + budget
rk = sc.rank_fixes(f); assert rk and all(x["points_removed"] > 0 for x in rk)
p = sc.plan_within_budget(f, 1000, {"screen_vents": 400, "clear_roof_debris": 300, "replace_roof": 50000})
assert p["cost"] <= 1000 and "replace_roof" not in p["fixes"] and p["after"] <= p["before"]
assert sc.plan_within_budget(f, 0, {"screen_vents": 400})["fixes"] == []
print("all scoring tests passed")


# ---- interior factor ----
def _with_interior(obs, n=3):
    g = dict(f); g["interior"] = {"n_images": n, "obs": dict(obs)}; return g
none_seen = {"fire_extinguisher": "not_seen", "sprinkler_head": "not_seen", "alarm_or_smoke_detector": "cannot_tell",
             "exit_sign": "not_seen", "blocked_egress": "not_seen", "stored_combustibles": "not_seen"}
base_i = sc.score(_with_interior(none_seen))
assert "interior" not in {m["factor"] for m in base_i["missing"]}
prot = sc.score(_with_interior({**none_seen, "fire_extinguisher": "seen", "sprinkler_head": "seen"}))
haz = sc.score(_with_interior({**none_seen, "blocked_egress": "seen"}))
assert prot["score"] < base_i["score"] < haz["score"], (prot["score"], base_i["score"], haz["score"])
assert sc.score(_with_interior(none_seen, n=0))["missing"], "0 images must count as missing"
gi = _with_interior({**none_seen, "blocked_egress": "seen", "stored_combustibles": "seen"})
assert sc.whatif(gi, ["clear_egress", "clear_stored_combustibles", "add_extinguishers"])["delta"] > 0
assert gi["interior"]["obs"]["blocked_egress"] == "seen"  # fixes never mutate the input
ids = {x["id"] for x in sc.rank_fixes(_with_interior(none_seen))}
assert "add_extinguishers" in ids and "clear_egress" not in ids  # only fixes that change something
print("interior tests passed")


# ---- regional (CAL FIRE zone) factor ----
def _z(zone):
    g = dict(f); g["zone_hazard"] = zone; return g
vh = sc.score(_z({"status": "ok", "in_zone": True, "class": "Very High", "area": "LRA (2011 map)"}))
out = sc.score(_z({"status": "ok", "in_zone": False, "class": None, "area": None}))
assert "regional" not in {m["factor"] for m in vh["missing"]} and vh["score"] > out["score"] > sc.score(f)["score"] - 100
assert "regional" in {m["factor"] for m in sc.score(_z({"status": "error"}))["missing"]}, "failed lookup = missing, not zero"
assert any("Very High" in e for x in vh["factors"] if x["key"] == "regional" for e in x["evidence"])
print("regional tests passed")


# ---- carbon-smart ranking ----
rk = sc.rank_fixes(f)
assert all(x["carbon"] in ("low", "med", "high") for x in rk)
cs = sc.carbon_smart(rk)
assert [x["id"] for x in cs] == [x["id"] for x in sorted(cs, key=lambda r: (-r["per_carbon"], -r["points_removed"]))]
assert all(set(x["id"] for x in cs) == set(x["id"] for x in rk) for _ in [0]), "same fixes, only reordered"
hi = sc.carbon_smart([{"id": "replace_roof", "label": "r", "cost": "high", "carbon": "high", "points_removed": 2},
                      {"id": "screen_vents", "label": "v", "cost": "low", "carbon": "low", "points_removed": 2}])
assert hi[0]["id"] == "screen_vents" and hi[1]["low_payoff"] and not hi[0]["low_payoff"]
print("carbon-smart tests passed")
