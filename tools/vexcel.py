"""Vexcel API 2.0 wrappers. Endpoints/params follow Vexcel_API_Hackathon_Tutorial.pdf.
Real mode calls the live Vexcel API (property/extract, ortho, sam3 detect); MOCK mode uses fixtures + a generated image."""
import json, random, time, hashlib
import requests
from PIL import Image, ImageDraw
from config import MOCK, VEXCEL_KEY, OUT_DIR, ROOT

API = "https://api.vexcelgroup.com/v2"
LAYERS = "urban,urban-r,wide-area,urban-g"
PALETTE = ["rgb(0,229,255)", "rgb(255,64,255)", "rgb(80,255,80)", "rgb(255,120,0)", "rgb(255,64,64)"]

def _send(method, path, **kw):
    for i in range(6):
        r = requests.request(method, f"{API}/{path}", headers={"X-API-KEY": VEXCEL_KEY}, **kw)
        if r.status_code != 429 or i == 5:
            return r
        time.sleep(min(2 ** i, 30) + random.random())

def _post(path, body, as_json=True):
    r = _send("POST", path, json=body, timeout=300)
    if not r.ok:
        raise RuntimeError(f"/{path} failed ({r.status_code}): {r.text[:300]}")
    return r.json() if as_json else r.content

def _square(lat, lon, half_m=40):  # WKT is LON first
    import math
    dlat = half_m / 111_320
    dlon = half_m / (111_320 * math.cos(math.radians(lat)))
    w, e, s, n = lon - dlon, lon + dlon, lat - dlat, lat + dlat
    return f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"

def _geom_wkt(g):
    from shapely.geometry import shape
    from shapely import wkt
    return wkt.loads(g).wkt if isinstance(g, str) else shape(g).wkt

def _key(lat, lon):
    return hashlib.md5(f"{lat:.5f},{lon:.5f}".encode()).hexdigest()[:8]

def get_property_elements(lat, lon):
    """Parcel polygon + Vexcel ML attributes. Returns {'attrs':..., 'parcel_wkt':...}."""
    if MOCK:
        d = json.load(open(ROOT / "fixtures/vexcel_property.json"))["features"][0]
        return {"attrs": d["properties"], "parcel_wkt": _square(lat, lon, 40), "mock": True}
    r = _send("GET", "property/extract", timeout=120, params={
        "wkt": f"POINT({lon} {lat})", "srid": 4326, "layer": LAYERS, "latest": "true",
        "include": "precomputed", "unit-system": "imperial"})
    if r.status_code == 404:  # no parcel here; imagery still works with any WKT
        return {"attrs": {}, "parcel_wkt": _square(lat, lon, 40), "mock": False, "note": "no parcel data"}
    r.raise_for_status()
    f = r.json()["features"][0]
    return {"attrs": f["properties"], "parcel_wkt": _geom_wkt(f["geometry"]), "mock": False}

def get_aerial(lat, lon, parcel_wkt=None) -> str:
    """Top-down ortho with parcel outlined. Returns file path."""
    path = OUT_DIR / f"ortho_{_key(lat, lon)}.jpg"
    if MOCK:
        _mock_ortho(path)
        return str(path)
    wkt = parcel_wkt or _square(lat, lon)
    img = _post("ortho/extract", {"wkt": wkt, "srid": 4326, "layer": LAYERS, "buffer-meter": 10,
                "crop": "bounding_box", "image-format": "jpeg",
                "drawing": [{"wkt": wkt, "outline-width": 4, "outline-color": "rgb(255,204,0)"}]}, as_json=False)
    path.write_bytes(img)
    return str(path)

def detect(lat, lon, labels, parcel_wkt=None, threshold=0.5):
    """Open-vocab detection (sam3) on ortho. Returns list of {n,label,confidence,bbox_wkt|box_pct}.
    TODO(real): obliques via oriented/collections + extra `images` entries; masks via pixel-mask."""
    if MOCK:
        d = json.load(open(ROOT / "fixtures/detections.json"))["detections"]
        return [{"n": i + 1, **x} for i, x in enumerate(d) if x["label"] in labels]
    wkt = parcel_wkt or _square(lat, lon)
    res = _post("elements/detect", {"wkt": wkt, "srid": 4326, "buffer-meter": 0,
        "images": [{"endpoint": "ortho/extract", "request": {"layer": LAYERS, "srid": 4326, "wkt": wkt, "crop": "bounding_box"}}],
        "model-input": {"elements": [{"label": l, "model": "sam3", "detection-threshold": threshold} for l in labels],
                        "include": ["bbox", "confidence"]}})
    out, n = [], 1
    for feat in res["features"]:
        for d in feat["properties"]["detections"]:
            out.append({"n": n, "label": feat["properties"]["label"], "confidence": round(d["confidence"], 2), "bbox_wkt": d["bbox"]})
            n += 1
    return out

def annotate(lat, lon, detections, parcel_wkt=None):
    """Numbered callouts on the ortho + close-up crop per detection. Returns {annotated, crops[]}."""
    k = _key(lat, lon)
    ann = OUT_DIR / f"annotated_{k}.jpg"
    if MOCK:
        base = Image.open(get_aerial(lat, lon)).convert("RGB")
        W, H = base.size
        dr = ImageDraw.Draw(base)
        crops = []
        for d in detections:
            x0, y0, x1, y1 = [int(v * s) for v, s in zip(d["box_pct"], (W, H, W, H))]
            dr.rectangle([x0, y0, x1, y1], outline=(255, 64, 64), width=4)
            dr.ellipse([x0 - 4, y0 - 4, x0 + 24, y0 + 24], fill=(255, 64, 64))
            dr.text((x0 + 5, y0 + 5), str(d["n"]), fill="white")
            cp = OUT_DIR / f"crop_{k}_{d['n']}.jpg"
            Image.open(get_aerial(lat, lon)).convert("RGB").crop((x0, y0, x1, y1)).resize((300, 300)).save(cp)
            crops.append({"n": d["n"], "label": d["label"], "path": str(cp)})
        base.save(ann)
        return {"annotated": str(ann), "crops": crops}
    wkt = parcel_wkt or _square(lat, lon)
    boxes = [{"wkt": d["bbox_wkt"], "outline-color": PALETTE[i % len(PALETTE)], "outline-width": 4,
              "label": f"{d['n']} {d['label']}", "label-color": PALETTE[i % len(PALETTE)], "label-size": 22}
             for i, d in enumerate(detections)]
    img = _post("ortho/extract", {"wkt": wkt, "srid": 4326, "layer": LAYERS, "buffer-meter": 10,
                "crop": "bounding_box", "image-format": "jpeg",
                "drawing": [{"wkt": wkt, "outline-width": 3, "outline-color": "rgb(255,204,0)"}] + boxes}, as_json=False)
    ann.write_bytes(img)
    def crop(d):  # close-up = ortho of the detection's own bbox (independent calls, so run them in parallel)
        cp = OUT_DIR / f"crop_{k}_{d['n']}.jpg"
        cp.write_bytes(_post("ortho/extract", {"wkt": d["bbox_wkt"], "srid": 4326, "layer": LAYERS, "buffer-meter": 5,
                       "crop": "bounding_box", "image-format": "jpeg", "destination-dimensions": "300,300"}, as_json=False))
        return {"n": d["n"], "label": d["label"], "path": str(cp)}
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(6) as ex:
        crops = list(ex.map(crop, detections))
    return {"annotated": str(ann), "crops": crops}

def _mock_ortho(path):
    if path.exists():
        return
    im = Image.new("RGB", (800, 800), (96, 110, 80))
    d = ImageDraw.Draw(im)
    d.rectangle([280, 80, 520, 330], fill=(150, 150, 150))   # roof
    d.rectangle([260, 60, 540, 340], outline=(255, 204, 0), width=4)
    d.rectangle([60, 80, 260, 330], fill=(40, 90, 40)); d.rectangle([540, 440, 780, 700], fill=(40, 90, 40))
    d.rectangle([320, 560, 520, 760], fill=(150, 130, 80)); d.rectangle([280, 80, 520, 330], outline=(80, 80, 80), width=2)
    d.text((10, 10), "MOCK ORTHO (fixture)", fill="white")
    im.save(path)
