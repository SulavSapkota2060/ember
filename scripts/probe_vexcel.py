"""One real Vexcel call on one building. Saves raw JSON + ortho to out/. Run from repo root."""
import sys, json
sys.path.insert(0, ".")
from config import MOCK, OUT_DIR
from tools import costar, vexcel
print("MOCK:", MOCK)
b = costar.list_buildings("Los Angeles")[0]
print(b)
r = vexcel._send("GET", "property/extract", timeout=120, params={
    "wkt": f"POINT({b['lon']} {b['lat']})", "srid": 4326, "layer": vexcel.LAYERS, "latest": "true",
    "include": "precomputed", "unit-system": "imperial"})
print("status", r.status_code)
if r.ok:
    (OUT_DIR / "probe_property.json").write_text(json.dumps(r.json(), indent=1))
    p = r.json()["features"][0]["properties"]
    print(len(p), "attrs")
    for k in sorted(p):
        if any(s in k for s in ("defensible", "tree", "roof-char", "structure.count", "building-type")):
            print(" ", k, "=", p[k])
else:
    print(r.text[:300])
