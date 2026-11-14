import json, threading
from config import LLM_MODEL
from hazards import HAZARDS
import scoring
from tools import costar, vexcel, hazard_zone, actions, matterport, interior

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

def get_interior(building_id: int) -> str:
    """Fetch Matterport interior images for the building and have a vision model list visible fire-protection
    items (extinguishers, sprinkler heads, alarms, exit signs) and hazards (blocked exits, stored combustibles).
    Returns per-item status seen/not_seen/cannot_tell with notes and the image paths. The sprinkler_head reading is
    unreliable (it can mistake HVAC diffusers) and is NOT scored: say so and rely on CoStar for sprinklers. These are uncertain
    observations from images: 'not_seen' is NOT proof of absence. Call BEFORE score_risk so it counts."""
    c = _ctx(building_id)
    models = c["prop"].get("matterport_models") or []
    if not models:
        return json.dumps({"status": "no_matterport_model"})
    if not matterport.available():
        return json.dumps({"status": "unavailable", "reason": "Matterport credentials not set"})
    with _lock:
        if "interior_raw" not in c:
            try:
                imgs = matterport.fetch_images(models)
                c["interior_raw"] = {"status": "ok", "images": imgs, "result": interior.analyze(imgs)} if imgs else \
                    {"status": "no_images"}
            except Exception as e:  # report, never invent
                c["interior_raw"] = {"status": "error", "reason": f"{type(e).__name__}: {str(e)[:200]}"}
    raw = c["interior_raw"]
    if raw["status"] != "ok":
        return json.dumps(raw)
    c["interior"] = {"n_images": raw["result"]["n_images"], "obs": raw["result"]["obs"]}
    r = raw["result"]
    return json.dumps({"status": "ok", "n_images": r["n_images"],
                       "images": [{"path": i["path"], "caption": i["caption"], "view_url": i["view_url"]}
                                  for i in raw["images"]],
                       "observations": {k: {"status": r["obs"][k], "note": r["notes"][k], "images": r["images_for"][k]}
                                        for k in r["obs"]}})

def score_risk(building_id: int) -> str:
    """Compute the fire-readiness score with the project's rule-based heuristic (code, not your judgement).
    Returns score 0-100, band, per-factor points with evidence, factors missing data, and ranked fixes
    (each with points_removed from the score and relative cost). Call this AFTER detect_hazard_features so detections count.
    Report this score exactly; never compute or adjust a score yourself."""
    c = _ctx(building_id)
    f = scoring.build_facts(c["prop"], c["el"]["attrs"], c.get("dets"), zone=_zone(c), interior=c.get("interior"))
    c["facts"] = f
    r = scoring.score(f)
    r["ranked_fixes"] = scoring.rank_fixes(f)
    r["detections_used"] = c.get("dets") is not None
    return json.dumps(r)

def get_stakes(building_id: int) -> str:
    """CoStar stakes: current tenants, known employees, occupied sq ft, last sale with price status. Values
    that are None are not recorded by CoStar; never estimate them."""
    return json.dumps(costar.get_stakes(building_id))

def _zone(c):
    """Official zone lookup, cached per building. Deterministic, so the score never depends on the LLM calling it."""
    if "zone" not in c:
        c["zone"] = hazard_zone.get_hazard_zone(c["prop"]["lat"], c["prop"]["lon"], _HAZARD)
    return c["zone"]

def get_facts(building_id) -> dict:
    """Scoring facts for a building (used by the UI for instant what-if). Builds them if score_risk has not run."""
    c = _ctx(building_id)
    if "facts" not in c:
        c["facts"] = scoring.build_facts(c["prop"], c["el"]["attrs"], c.get("dets"), zone=_zone(c), interior=c.get("interior"))
    return c["facts"]

def what_if(building_id: int, fix_ids: list[str]) -> str:
    """Re-score the building as if the given fixes were done. fix_ids come from score_risk's ranked_fixes
    (ids like clear_zone1, screen_vents). Returns before/after score and which factors moved. Code, not judgement."""
    bad = [i for i in fix_ids if i not in scoring.FIXES]
    if bad:
        return json.dumps({"error": f"unknown fix ids {bad}", "valid": list(scoring.FIXES)})
    return json.dumps(scoring.whatif(get_facts(building_id), fix_ids))

def plan_budget(building_id: int, budget: float, costs: dict[str, float]) -> str:
    """Best set of fixes within a budget. `costs` maps fix id -> dollars and MUST come from the user; never
    invent prices. Fixes without a user cost are returned in skipped_no_cost."""
    return json.dumps(scoring.plan_within_budget(get_facts(building_id), float(budget),
                                                 {k: float(v) for k, v in costs.items()}))

def get_hazard_zone(building_id: int) -> str:
    """Official CAL FIRE Fire Hazard Severity Zone at the building (wildfire). Returns in_zone and class
    (Moderate/High/Very High). 'in_zone: false' means outside the mapped zones in an older service, which is
    not proof of low hazard. Status 'error'/'unavailable': say so."""
    return json.dumps(_zone(_ctx(building_id)))

def draft_action(building_id: int, subject: str, body: str) -> str:
    """Save a drafted action (e.g. contractor request email). Returns the file path."""
    return actions.write_action_draft(building_id, subject, body)

TOOLS = [get_property, get_parcel_attributes, get_aerial, detect_hazard_features, get_hazard_zone,
         get_stakes, get_interior, score_risk, what_if, plan_budget, draft_action]

SYSTEM = """You are Ember, a hazard-readiness analyst for commercial buildings.
{hazard_prompt}
Process: write a plan with the todo tool first and update item statuses as you go. Gather data with the
data tools (these can run together), then call detect_hazard_features and get_interior (both feed the score),
THEN score_risk, then report.
The score comes only from score_risk: report its number, band and factors exactly. Never compute, adjust or
estimate a score yourself.
Report format: (1) score 0-100 labelled ESTIMATE with 3-5 reasons that cite the evidence strings returned
by score_risk (and callout numbers when relevant); (2) fix list taken from score_risk's ranked_fixes, with
its points_removed written as "-N points" (a reduction, never "+N") and relative cost (low/med/high), no dollar figures unless the user gave a budget;
(3) what is at stake, from get_stakes (tenants, known employees, last sale with its price status; say
"not recorded" for None values, never estimate); (4) interior: what get_interior saw, as uncertain image readings (say not_seen is not proof of absence; if its
status is not ok, say why); (5) resilience: 2-3 sentences on why the top fixes matter for sustainability and climate adaptation
(protecting an existing building and its tenants avoids loss, displacement and rebuilding; wildfire risk is
rising, so exposure fixes are adaptation). Qualitative only: no carbon, dollar or percentage figures unless a tool
returned them, and say "not quantified"; (6) data gaps: list score_risk's `missing` factors, hazard zone unavailable, and mock data if any.
Follow-up questions: use what_if for "what if I do X" and plan_budget for budget questions (ask the user for
per-fix dollar costs if they gave none; never invent prices). Quote results exactly; deltas are reductions
("-N points"). Keep answers short.
Rules: the score is a transparent heuristic, not a validated model. Never invent data a tool did not
return. Do not state distances or units for defensible-space zones (the data does not define them): say
"zone 1 (nearest the building)" and quote the percentages as given. The roof condition value has an
undocumented scale: do not interpret it. Images are shown to the user by the app; you cannot open image
files, so do not try to read them. If a tool result says mock, state that."""

def build_agent(hazard: str, memory: bool = False):
    if not LLM_MODEL:
        raise RuntimeError("LLM_MODEL not set in .env (format 'provider:model'). Provider undecided.")
    global _HAZARD
    _HAZARD = hazard
    costar._props(); costar._models()  # warm the cache once, before parallel tool threads
    from deepagents import create_deep_agent
    from langchain.agents.middleware import TodoListMiddleware  # not in deepagents 0.7.x defaults; powers the live plan
    kw = {}
    if memory:  # keeps the conversation per thread_id so follow-up chat has context
        from langgraph.checkpoint.memory import InMemorySaver
        kw["checkpointer"] = InMemorySaver()
    return create_deep_agent(model=LLM_MODEL, tools=TOOLS, middleware=[TodoListMiddleware()],
                             system_prompt=SYSTEM.format(hazard_prompt=HAZARDS[hazard]["prompt"]), **kw)

def stream_events(agent, building_id: int, hazard: str, user_msg: str | None = None, thread_id: str | None = None):
    """Yield UI events: {'type': 'plan'|'tool_call'|'tool_result'|'text', ...}."""
    msg = user_msg or f"Assess {hazard} readiness for building_id={building_id}."
    cfg = {"configurable": {"thread_id": thread_id}} if thread_id else None
    for chunk in agent.stream({"messages": [{"role": "user", "content": msg}]}, config=cfg, stream_mode="updates"):
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
                    yield {"type": "tool_result", "name": m.name, "content": str(m.content)[:20000 if m.name in ("detect_hazard_features", "get_interior") else 2000]}
