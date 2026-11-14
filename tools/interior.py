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


CONFUSIONS = {
    "fire_extinguisher": "a red box, hose reel, thermostat, speaker, heater or any other wall fixture",
    "sprinkler_head": "an HVAC air diffuser/vent, speaker, light fixture, smoke detector or sensor",
    "alarm_or_smoke_detector": "a thermostat, motion sensor, speaker, light or a generic ceiling disc",
    "exit_sign": "any other sign or a light",
    "blocked_egress": "furniture or equipment that does NOT actually obstruct the route",
    "stored_combustibles": "ordinary furniture or fixtures that are not stored material",
}
VERIFY_PROMPT = """A first reviewer claims the items below are visible in these interior images. Be skeptical.
Confirm an item ONLY if it is unambiguously that item and cannot reasonably be something else.
Typical confusions: {conf}
Claims:
{claims}
Reply with JSON only: {{"<item_key>": {{"verdict": "confirmed|not_confirmed", "reason": "short, factual"}}}}"""


def _text(resp):
    return resp.content if isinstance(resp.content, str) else "".join(
        b.get("text", "") for b in resp.content if isinstance(b, dict))


def verify(images: list[dict], result: dict) -> dict:
    """Second, skeptical vision pass over every item the first pass called 'seen'. Anything not confirmed is
    downgraded to cannot_tell, so a single misread (e.g. HVAC diffuser as sprinkler) cannot reach the score."""
    seen = [k for k, v in result["obs"].items() if v == "seen"]
    result["first_pass"] = dict(result["obs"])
    result["second_look"] = {}
    if not seen:
        return result
    from langchain.chat_models import init_chat_model
    idx = sorted({n for k in seen for n in (result["images_for"].get(k) or []) if 1 <= n <= len(images)}) \
        or list(range(1, len(images) + 1))
    claims = "\n".join(f"- {k}: {result['notes'].get(k, '')} (first reviewer cited images {result['images_for'].get(k)})"
                       for k in seen)
    content = [{"type": "text", "text": VERIFY_PROMPT.format(
        conf="; ".join(f"{k}: {CONFUSIONS[k]}" for k in seen), claims=claims)}]
    for n in idx:
        content.append({"type": "text", "text": f"Image {n}: {images[n - 1]['caption']}"})
        content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + _b64(images[n - 1]["path"])}})
    txt = _text(init_chat_model(VISION_MODEL).invoke([{"role": "user", "content": content}]))
    try:
        verdicts = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
    except Exception:
        verdicts = {}
    for k in seen:
        v = verdicts.get(k) or {}
        ok = v.get("verdict") == "confirmed"
        result["second_look"][k] = {"verdict": "confirmed" if ok else "not_confirmed", "reason": str(v.get("reason", ""))[:160]}
        if not ok:
            result["obs"][k] = "cannot_tell"
            result["notes"][k] = ("Second look could not confirm: " + (str(v.get("reason", "")) or "no reason given"))[:160]
    return result


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
    txt = _text(init_chat_model(VISION_MODEL).invoke([{"role": "user", "content": content}]))
    obs = json.loads(txt[txt.index("{"): txt.rindex("}") + 1]).get("observations", {})
    out = {"obs": {}, "notes": {}, "images_for": {}, "n_images": len(images)}
    for k in ITEMS:
        o = obs.get(k) or {}
        st = o.get("status") if o.get("status") in ("seen", "not_seen", "cannot_tell") else "cannot_tell"
        out["obs"][k], out["notes"][k], out["images_for"][k] = st, str(o.get("note", ""))[:160], o.get("images") or []
    return out
