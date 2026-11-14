"""Screen every LA non-apartment Matterport building with Vexcel property/extract.
Writes out/screen_la.json (resumable) and prints a ranked table. Run from repo root."""
import sys, json, time
sys.path.insert(0, ".")
from config import OUT_DIR
from tools import costar, vexcel

OUTF = OUT_DIR / "screen_la.json"
done = json.loads(OUTF.read_text()) if OUTF.exists() else {}
blds = costar.list_buildings("Los Angeles", limit=10000)
blds = [b for b in blds if b["matterport"]]
print(len(blds), "buildings;", len(done), "already done")

for i, b in enumerate(blds):
    if str(b["id"]) in done:
        continue
    try:
        r = vexcel._send("GET", "property/extract", timeout=120, params={
            "wkt": f"POINT({b['lon']} {b['lat']})", "srid": 4326, "layer": vexcel.LAYERS,
            "latest": "true", "include": "precomputed", "unit-system": "imperial"})
        if r.status_code == 404:
            row = {"status": 404}
        elif not r.ok:
            row = {"status": r.status_code}
        else:
            p = r.json()["features"][0]["properties"]
            s = next((x for x in p.get("structures", []) if x.get("primary-structure")), (p.get("structures") or [{}])[0])
            g = lambda k: s.get(k)
            row = {"status": 200, "collection": p.get("metadata.collection"), "structs": p.get("structure.count"),
                   "z1t": g("defensible-space.zone1-tree"), "z2t": g("defensible-space.zone2-tree"),
                   "z3t": g("defensible-space.zone3-tree"), "z1b": g("defensible-space.zone1-building"),
                   "z4b": g("defensible-space.zone4-building"), "roof": g("roof-characteristics.material.class"),
                   "cond": g("roof-characteristics.condition"), "disc": g("roof-defects.discoloration.percent"),
                   "elev": g("ground-elevation.value"), "fp": g("footprint.area")}
    except Exception as e:
        row = {"status": "err", "err": str(e)[:100]}
    done[str(b["id"])] = {**{k: b[k] for k in ("id", "name", "type", "lat", "lon")}, **row}
    OUTF.write_text(json.dumps(done))
    if i % 10 == 0:
        print(i, "/", len(blds), flush=True)
    time.sleep(0.3)

ok = [v for v in done.values() if v.get("status") == 200 and v.get("z1t") is not None]
ok.sort(key=lambda v: -((v["z1t"] or 0) * 2 + (v["z2t"] or 0)))
print("\nstatus counts:", {s: sum(1 for v in done.values() if v.get("status") == s) for s in {v.get("status") for v in done.values()}})
print("top by vegetation near structure (2*zone1 + zone2):")
for v in ok[:15]:
    print(f"{v['id']:>7} {str(v['name'])[:26]:26} {v['type']:10} z1={v['z1t']:>5} z2={v['z2t']:>5} z3={v['z3t']:>5} z4bldg={v['z4b']} roof={v['roof']} cond={v['cond']} elev={round(v['elev'] or 0)}")
