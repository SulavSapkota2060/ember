"""Real ortho + detect + annotate for given building ids. Usage: python scripts/probe_building.py ID [ID...]"""
import sys, json, time
sys.path.insert(0, ".")
from tools import costar, vexcel
LABELS = ["tree", "dry brush", "wood deck", "wood fence", "car"]
for pid in map(int, sys.argv[1:]):
    p = costar.get_property(pid)
    el = vexcel.get_property_elements(p["lat"], p["lon"])
    print(pid, p["name"], p["address"], "| parcel:", el.get("note", "ok"))
    t = time.time()
    img = vexcel.get_aerial(p["lat"], p["lon"], el["parcel_wkt"])
    print(" ortho", img, round(time.time() - t, 1), "s")
    t = time.time()
    try:
        dets = vexcel.detect(p["lat"], p["lon"], LABELS, el["parcel_wkt"])
        print(" detect", round(time.time() - t, 1), "s;", [(d["n"], d["label"], d["confidence"]) for d in dets])
        a = vexcel.annotate(p["lat"], p["lon"], dets, el["parcel_wkt"])
        print(" annotated", a["annotated"], len(a["crops"]), "crops")
    except Exception as e:
        print(" detect/annotate FAILED:", str(e)[:300])
