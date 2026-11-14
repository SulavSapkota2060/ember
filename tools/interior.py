"""Vision pass over Matterport interior images. A model READS images: output is an uncertain observation,
never proof. 'not_seen' means not visible in the images given, not that the item is absent."""
import base64, json
from config import VISION_MODEL

ITEMS = {
    "fire_extinguisher": "fire extinguisher (red cylinder or cabinet/signage for one)",
    "sprinkler_head": "fire sprinkler head: a SMALL fitting (glass bulb or deflector) on a ceiling pipe. Round air-conditioning diffusers, vents, speakers and light fixtures are NOT sprinkler heads",
    "alarm_or_smoke_detector": "fire alarm pull station, horn/strobe, or smoke/heat detector",
    "exit_sign": "illuminated or posted EXIT sign",
    "blocked_egress": "a door, exit path or corridor visibly blocked by furniture, boxes or equipment (hazard)",
    "stored_combustibles": "visible stored combustibles such as stacked boxes, paper, cardboard or fabric (hazard)",
}
PROMPT = """You are checking interior photos of one commercial building for fire-protection items.
Rules: report ONLY what is clearly visible. If an image is unclear or the item could be hidden, say cannot_tell.
Do not guess and do not infer from the building type. If an object could plausibly be something else (for example an HVAC diffuser, speaker or light), use cannot_tell. 'not_seen' means not visible in these images.
Images are numbered in order. Items to check:
{items}
Reply with JSON only, no prose, exactly this shape:
{{"observations": {{"<item_key>": {{"status": "seen|not_seen|cannot_tell", "images": [1], "note": "short, factual"}}}}}}
Use these item keys: {keys}."""


def _b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def analyze(images: list[dict]) -> dict:
    """images: [{path, caption}]. Returns {"obs": {key: status}, "notes": {key: str}, "images_for": {key: [n]},
    "n_images": N}. Raises on model/parse failure (caller reports it)."""
    from langchain.chat_models import init_chat_model
    content = [{"type": "text", "text": PROMPT.format(
        items="\n".join(f"- {k}: {v}" for k, v in ITEMS.items()), keys=", ".join(ITEMS))}]
    for n, im in enumerate(images, 1):
        content.append({"type": "text", "text": f"Image {n}: {im['caption']}"})
        content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + _b64(im["path"])}})
    resp = init_chat_model(VISION_MODEL).invoke([{"role": "user", "content": content}])
    txt = resp.content if isinstance(resp.content, str) else "".join(
        b.get("text", "") for b in resp.content if isinstance(b, dict))
    obs = json.loads(txt[txt.index("{"): txt.rindex("}") + 1]).get("observations", {})
    out = {"obs": {}, "notes": {}, "images_for": {}, "n_images": len(images)}
    for k in ITEMS:
        o = obs.get(k) or {}
        st = o.get("status") if o.get("status") in ("seen", "not_seen", "cannot_tell") else "cannot_tell"
        out["obs"][k], out["notes"][k], out["images_for"][k] = st, str(o.get("note", ""))[:160], o.get("images") or []
    return out
