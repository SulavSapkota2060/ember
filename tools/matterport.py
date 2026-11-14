"""Matterport: fetch interior images for a building's models (GraphQL, Basic auth id:secret).
Verified 2026-11-14 on CoStar property 249226: model thumbnails and panorama skybox faces (6 per pano,
resolutions low/2k/4k/high) come back as signed, expiring URLs. Matterport's own object/room labels are empty
for these models, so interior facts come from a vision pass (tools/interior.py)."""
import base64, io
import requests
from PIL import Image
from config import OUT_DIR, MATTERPORT_ID, MATTERPORT_SECRET

URL = "https://api.matterport.com/api/models/graph"


def available() -> bool:
    return bool(MATTERPORT_ID and MATTERPORT_SECRET)


def view_url(model_id: str) -> str:
    return f"https://my.matterport.com/show/?m={model_id}"


def _gql(query: str) -> dict:
    auth = base64.b64encode(f"{MATTERPORT_ID}:{MATTERPORT_SECRET}".encode()).decode()
    r = requests.post(URL, json={"query": query}, headers={"Authorization": f"Basic {auth}"}, timeout=60)
    r.raise_for_status()
    j = r.json()
    if j.get("errors") and not j.get("data"):
        raise RuntimeError("Matterport GraphQL error: " + str(j["errors"])[:200])
    return j["data"]


def _img(url: str) -> Image.Image:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def _one_model(i: int, mid: str, sheets: int) -> list[dict]:
    out = []
    d = _gql('{ model(id:"%s") { name image { url } panoLocations { skybox(resolution:"low") { children } } } }' % mid)
    m = d.get("model") or {}
    name = m.get("name") or mid
    thumb = (m.get("image") or {}).get("url")
    panos = [x for x in (m.get("panoLocations") or []) if (x.get("skybox") or {}).get("children")]
    from concurrent.futures import ThreadPoolExecutor
    faces_urls = panos[len(panos) // 2]["skybox"]["children"][:6] if (i < sheets and panos) else []
    with ThreadPoolExecutor(7) as ex:  # thumbnail + cube faces are independent downloads
        f_thumb = ex.submit(_img, thumb) if thumb else None
        f_faces = [ex.submit(_img, u) for u in faces_urls]
        if f_thumb:
            im = f_thumb.result()
            im.thumbnail((1024, 1024))
            p = OUT_DIR / f"mp_{mid}_thumb.jpg"
            im.save(p, quality=85)
            out.append({"path": str(p), "caption": f"{name} (model thumbnail)", "model_id": mid,
                        "model_name": name, "kind": "thumbnail", "view_url": view_url(mid)})
        if f_faces:
            sheet = Image.new("RGB", (1200, 800))
            for k, f in enumerate(f_faces):
                sheet.paste(f.result().resize((400, 400)), ((k % 3) * 400, (k // 3) * 400))
            p = OUT_DIR / f"mp_{mid}_sheet.jpg"
            sheet.save(p, quality=85)
            out.append({"path": str(p), "caption": f"{name} (one panorama, all 6 cube faces tiled, incl. ceiling)",
                        "model_id": mid, "model_name": name, "kind": "panorama", "view_url": view_url(mid)})
    return out


def fetch_images(model_ids: list[str], max_models: int = 4, sheets: int = 2) -> list[dict]:
    """Per model: its thumbnail. For the first `sheets` models also a contact sheet of one mid-tour panorama (all 6
    cube faces tiled 3x2, so ceilings are visible too). Saved under out/. Models and downloads run in parallel."""
    from concurrent.futures import ThreadPoolExecutor
    ids = model_ids[:max_models]
    with ThreadPoolExecutor(max(1, len(ids))) as ex:
        parts = list(ex.map(lambda t: _one_model(t[0], t[1], sheets), enumerate(ids)))
    return [x for p in parts for x in p]
