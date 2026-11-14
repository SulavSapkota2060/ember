import json
from config import LLM_MODEL
from hazards import HAZARDS
from tools import costar, vexcel, hazard_zone, actions

_cache = {}  # building_id -> {"prop":..., "elements":...}

def _ctx(building_id):
    building_id = int(building_id)
    if building_id not in _cache:
        p = costar.get_property(building_id)
        _cache[building_id] = {"prop": p, "el": vexcel.get_property_elements(p["lat"], p["lon"])}
    return _cache[building_id]

# ---- agent tools (docstrings are what the LLM sees) ----
def get_property(building_id: int) -> str:
    """CoStar record for a building: address, type, size, class, sprinklers, Matterport model ids."""
    return json.dumps(costar.get_property(building_id))

def get_parcel_attributes(building_id: int) -> str:
    """Vexcel machine-learned attributes for the building's parcel (tree cover, defensible-space zones,
    roof material/condition, footprint). Keys are dotted strings."""
    return json.dumps(_ctx(building_id)["el"]["attrs"])

def get_aerial(building_id: int) -> str:
    """Fetch the top-down Vexcel aerial image of the parcel. Returns the saved image path."""
    c = _ctx(building_id)
    return vexcel.get_aerial(c["prop"]["lat"], c["prop"]["lon"], c["el"]["parcel_wkt"])

def detect_hazard_features(building_id: int, hazard: str) -> str:
    """Run Vexcel open-vocabulary detection for hazard-relevant features on the aerial image, then produce
    an annotated image with numbered callouts plus a close-up crop per detection. Returns detections
    (n, label, confidence) and image paths. Use the callout numbers in your report."""
    c = _ctx(building_id)
    p, wkt = c["prop"], c["el"]["parcel_wkt"]
    dets = vexcel.detect(p["lat"], p["lon"], HAZARDS[hazard]["detect_labels"], wkt)
    ann = vexcel.annotate(p["lat"], p["lon"], dets, wkt)
    slim = [{k: v for k, v in d.items() if k in ("n", "label", "confidence")} for d in dets]
    return json.dumps({"detections": slim, **ann})

def get_hazard_zone(building_id: int, hazard: str) -> str:
    """Official hazard-zone lookup. May return status 'unavailable': then say so and score from imagery."""
    p = _ctx(building_id)["prop"]
    return json.dumps(hazard_zone.get_hazard_zone(p["lat"], p["lon"], hazard))

def draft_action(building_id: int, subject: str, body: str) -> str:
    """Save a drafted action (e.g. contractor request email). Returns the file path."""
    return actions.write_action_draft(building_id, subject, body)

TOOLS = [get_property, get_parcel_attributes, get_aerial, detect_hazard_features, get_hazard_zone, draft_action]

SYSTEM = """You are Ember, a hazard-readiness analyst for commercial buildings.
{hazard_prompt}
Process: write a plan with the todo tool first, then gather data with tools, then report.
Report format: (1) risk score 0-100 labelled ESTIMATE with 3-5 reasons that cite evidence (callout numbers,
attribute values); (2) prioritized fix list with rough relative cost (low/med/high), no invented dollar figures
unless the user gave a budget; (3) data gaps (e.g. hazard zone unavailable, mock data).
The score is a transparent heuristic, not a validated model. Never invent data a tool did not return.
If a tool result says mock, state that."""

def build_agent(hazard: str):
    if not LLM_MODEL:
        raise RuntimeError("LLM_MODEL not set in .env (format 'provider:model'). Provider undecided.")
    from deepagents import create_deep_agent
    return create_deep_agent(model=LLM_MODEL, tools=TOOLS,
                             system_prompt=SYSTEM.format(hazard_prompt=HAZARDS[hazard]["prompt"]))

def stream_events(agent, building_id: int, hazard: str, user_msg: str | None = None):
    """Yield UI events: {'type': 'plan'|'tool_call'|'tool_result'|'text', ...}."""
    msg = user_msg or f"Assess {hazard} readiness for building_id={building_id}."
    for chunk in agent.stream({"messages": [{"role": "user", "content": msg}]}, stream_mode="updates"):
        for _node, upd in (chunk or {}).items():
            if not isinstance(upd, dict):
                continue
            if upd.get("todos"):
                yield {"type": "plan", "todos": upd["todos"]}
            for m in upd.get("messages", []) or []:
                kind = getattr(m, "type", "")
                if kind == "ai":
                    for tc in getattr(m, "tool_calls", []) or []:
                        yield {"type": "tool_call", "name": tc["name"], "args": tc["args"]}
                    txt = m.content if isinstance(m.content, str) else "".join(
                        b.get("text", "") for b in m.content if isinstance(b, dict))
                    if txt.strip() and not getattr(m, "tool_calls", None):
                        yield {"type": "text", "text": txt}
                elif kind == "tool":
                    yield {"type": "tool_result", "name": m.name, "content": str(m.content)[:2000]}
