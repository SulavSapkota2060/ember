import json, threading
from config import LLM_MODEL
from hazards import HAZARDS
import scoring
from tools import costar, vexcel, hazard_zone, actions

_cache = {}
_lock = threading.Lock()
_HAZARD = "wildfire"  # set by build_agent; tools read it so the LLM never passes it  # building_id -> {"prop":..., "elements":...}

def _ctx(building_id):
    building_id = int(building_id)
    with _lock:  # tools run in parallel threads; fetch each building once
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

def detect_hazard_features(building_id: int) -> str:
    """Run Vexcel open-vocabulary detection for hazard-relevant features on the aerial image, then produce
    an annotated image with numbered callouts plus a close-up crop per detection. Returns detections
    (n, label, confidence) and image paths. Use the callout numbers in your report."""
    c = _ctx(building_id)
    p, wkt = c["prop"], c["el"]["parcel_wkt"]
    dets = vexcel.detect(p["lat"], p["lon"], HAZARDS[_HAZARD]["detect_labels"], wkt)
    ann = vexcel.annotate(p["lat"], p["lon"], dets, wkt)
    c["dets"] = dets  # score_risk reads these
    slim = [{k: v for k, v in d.items() if k in ("n", "label", "confidence")} for d in dets]
    return json.dumps({"detections": slim, **ann})

def score_risk(building_id: int) -> str:
    """Compute the fire-readiness score with the project's rule-based heuristic (code, not your judgement).
    Returns score 0-100, band, per-factor points with evidence, factors missing data, and ranked fixes
    (each with score delta and relative cost). Call this AFTER detect_hazard_features so detections count.
    Report this score exactly; never compute or adjust a score yourself."""
    c = _ctx(building_id)
    f = scoring.build_facts(c["prop"], c["el"]["attrs"], c.get("dets"))
    c["facts"] = f
    r = scoring.score(f)
    r["ranked_fixes"] = scoring.rank_fixes(f)
    r["detections_used"] = c.get("dets") is not None
    return json.dumps(r)

def get_hazard_zone(building_id: int) -> str:
    """Official hazard-zone lookup. May return status 'unavailable': then say so and score from imagery."""
    p = _ctx(building_id)["prop"]
    return json.dumps(hazard_zone.get_hazard_zone(p["lat"], p["lon"], _HAZARD))

def draft_action(building_id: int, subject: str, body: str) -> str:
    """Save a drafted action (e.g. contractor request email). Returns the file path."""
    return actions.write_action_draft(building_id, subject, body)

TOOLS = [get_property, get_parcel_attributes, get_aerial, detect_hazard_features, get_hazard_zone,
         score_risk, draft_action]

SYSTEM = """You are Ember, a hazard-readiness analyst for commercial buildings.
{hazard_prompt}
Process: write a plan with the todo tool first and update item statuses as you go. Gather data with the
data tools (these can run together), then call detect_hazard_features, THEN score_risk (it needs the
detections), then report.
The score comes only from score_risk: report its number, band and factors exactly. Never compute, adjust or
estimate a score yourself.
Report format: (1) score 0-100 labelled ESTIMATE with 3-5 reasons that cite the evidence strings returned
by score_risk (and callout numbers when relevant); (2) fix list taken from score_risk's ranked_fixes, with
its score deltas and relative cost (low/med/high), no dollar figures unless the user gave a budget;
(3) data gaps: list score_risk's `missing` factors, hazard zone unavailable, and mock data if any.
Rules: the score is a transparent heuristic, not a validated model. Never invent data a tool did not
return. Do not state distances or units for defensible-space zones (the data does not define them): say
"zone 1 (nearest the building)" and quote the percentages as given. The roof condition value has an
undocumented scale: do not interpret it. Images are shown to the user by the app; you cannot open image
files, so do not try to read them. If a tool result says mock, state that."""

def build_agent(hazard: str):
    if not LLM_MODEL:
        raise RuntimeError("LLM_MODEL not set in .env (format 'provider:model'). Provider undecided.")
    global _HAZARD
    _HAZARD = hazard
    costar._props(); costar._models()  # warm the cache once, before parallel tool threads
    from deepagents import create_deep_agent
    from langchain.agents.middleware import TodoListMiddleware  # not in deepagents 0.7.x defaults; powers the live plan
    return create_deep_agent(model=LLM_MODEL, tools=TOOLS, middleware=[TodoListMiddleware()],
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
