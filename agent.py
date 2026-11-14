import json, threading
from config import LLM_MODEL
from hazards import HAZARDS
import scoring, sustainability
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
    c["detect_result"] = {"detections": slim, **ann}  # the UI reads this from the cache (subagent tool calls are not streamed to it)
    return json.dumps(c["detect_result"])

def get_interior(building_id: int) -> str:
    """Fetch Matterport interior images for the building and have a vision model list visible fire-protection
    items (extinguishers, sprinkler heads, alarms, exit signs) and hazards (blocked exits, stored combustibles).
    Returns per-item status seen/not_seen/cannot_tell with notes and the image paths. Every 'seen' item gets a second skeptical look and is downgraded to cannot_tell unless
    confirmed (see second_look). The sprinkler_head reading is
    unreliable (it can mistake HVAC diffusers) and is NOT scored: say so and rely on CoStar for sprinklers. These are uncertain
    observations from images: 'not_seen' is NOT proof of absence. Call BEFORE score_risk so it counts."""
    c = _ctx(building_id)
    models = c["prop"].get("matterport_models") or []
    if not models:
        c["interior_raw"] = {"status": "no_matterport_model"}
        return json.dumps(c["interior_raw"])
    if not matterport.available():
        c["interior_raw"] = {"status": "unavailable", "reason": "Matterport credentials not set"}
        return json.dumps(c["interior_raw"])
    with _lock:
        if "interior_raw" not in c:
            try:
                imgs = matterport.fetch_images(models)
                c["interior_raw"] = {"status": "ok", "images": imgs, "result": interior.verify(imgs, interior.analyze(imgs))} if imgs else \
                    {"status": "no_images"}
            except Exception as e:  # report, never invent
                c["interior_raw"] = {"status": "error", "reason": f"{type(e).__name__}: {str(e)[:200]}"}
    return json.dumps(_interior_view(c))

def _interior_view(c) -> dict:
    raw = c.get("interior_raw") or {"status": "not_run"}
    if raw["status"] != "ok":
        return raw
    r = raw["result"]
    c["interior"] = {"n_images": r["n_images"], "obs": r["obs"]}  # what score_risk reads
    return {"status": "ok", "n_images": r["n_images"],
            "images": [{"path": i["path"], "caption": i["caption"], "view_url": i["view_url"]} for i in raw["images"]],
            "observations": {k: {"status": r["obs"][k], "note": r["notes"][k], "images": r["images_for"][k],
                                 "first_pass": r["first_pass"][k],
                                 "second_look": (r["second_look"].get(k) or {}).get("verdict")} for k in r["obs"]}}

def get_view(building_id) -> dict:
    """Everything the UI needs to draw, read from the cache (works whether tools ran in the orchestrator or a subagent)."""
    c = _ctx(building_id)
    return {"images": c.get("detect_result"), "interior": _interior_view(c)}

def score_risk(building_id: int) -> str:
    """Compute the fire EXPOSURE score (higher = more exposed, lower is better) with the project's rule-based heuristic (code, not your judgement).
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
    out = costar.get_stakes(building_id)
    out["embodied_carbon_at_stake"] = sustainability.carbon_at_stake(costar.get_property(building_id).get("gba_sqft"))
    return json.dumps(out)

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

SYSTEM_SOLO = """You are Ember, a hazard-readiness analyst for commercial buildings.
{hazard_prompt}
Process: write a plan with the todo tool first and update item statuses as you go. Gather data with the
data tools (these can run together), then call detect_hazard_features and get_interior (both feed the score),
THEN score_risk, then report.
The score comes only from score_risk: report its number, band and factors exactly. Never compute, adjust or
estimate a score yourself.
Report format: (1) exposure score 0-100 labelled ESTIMATE (higher = more exposed, lower is better; never call it a
"readiness" score) with 3-5 reasons that cite the evidence strings returned
by score_risk (and callout numbers when relevant); (2) fix list taken from score_risk's ranked_fixes, with
its points_removed written as "-N points" (a reduction, never "+N"), relative cost (low/med/high) and relative carbon tag
(our heuristic, not sourced; call a high-carbon fix removing 3 or fewer points "low payoff for its carbon", and mention a
fix's tradeoff text if present), no dollar figures unless the user gave a budget;
(3) what is at stake, from get_stakes (tenants, known employees, last sale with its price status; say
"not recorded" for None values, never estimate); (4) interior: what get_interior saw, as uncertain image readings (say not_seen is not proof of absence; if its
status is not ok, say why); (5) resilience: 2-3 sentences on why the top fixes matter for sustainability and climate adaptation
(protecting an existing building and its tenants avoids loss, displacement and rebuilding; wildfire risk is
rising, so exposure fixes are adaptation). Use get_stakes' embodied_carbon_at_stake (tonnes CO2e) as "carbon at stake" if present,
never as "avoided" or "saved": it is upfront A1-A3 carbon a rebuild would re-emit, a US commercial average with no
probability of loss applied. State those caveats once. No other carbon, dollar or percentage figures unless a tool
returned them, and say "not quantified" for benefits; (6) data gaps: list score_risk's `missing` factors, hazard zone unavailable, and mock data if any.
Follow-up questions: use what_if for "what if I do X" and plan_budget for budget questions (ask the user for
per-fix dollar costs if they gave none; never invent prices). Quote results exactly; deltas are reductions
("-N points"). Keep answers short.
Rules: the score is a transparent heuristic, not a validated model. Never invent data a tool did not
return. Do not state distances or units for defensible-space zones (the data does not define them): say
"zone 1 (nearest the building)" and quote the percentages as given. The roof condition value has an
undocumented scale: do not interpret it. Images are shown to the user by the app; you cannot open image
files, so do not try to read them. If a tool result says mock, state that."""


# ---- team mode: an orchestrator that delegates data gathering to three specialist subagents ----
_SUB_RULES = ("Report facts only, quoting numbers exactly as the tools returned them; never compute a risk score, "
              "never invent data, say 'not recorded' for missing values. You cannot open image files. Be concise: "
              "reply with a short factual summary, not raw JSON.")

def _subagents():
    from config import LIGHT_MODEL
    return [
        {"name": "exterior-analyst",
         "description": "Outside exposure for one building_id: parcel attributes (tree cover by zone, roof, vents), "
                        "official CAL FIRE hazard zone, aerial detection with numbered callouts. Give it the building_id.",
         "system_prompt": "You are the exterior analyst. Call get_property, get_parcel_attributes, get_aerial, "
                          "get_hazard_zone, then detect_hazard_features for the building_id you are given. Summarise: "
                          "tree cover per zone, roof material/defects/vents, hazard zone result, detection callouts. "
                          "Do not state distances for defensible-space zones; do not interpret the roof condition value. "
                          + _SUB_RULES,
         "tools": [get_property, get_parcel_attributes, get_aerial, get_hazard_zone, detect_hazard_features]},
        {"name": "interior-inspector",
         "description": "Inside protection for one building_id from Matterport images (vision, second-look verified): "
                        "extinguishers, alarms, exit signs, blocked exits, stored combustibles. Give it the building_id.",
         "system_prompt": "You are the interior inspector. Call get_interior once for the building_id you are given and "
                          "summarise each item's final status, noting which were downgraded by the second look. These are "
                          "uncertain image readings: 'not_seen' is not proof of absence; sprinkler_head is unreliable and "
                          "not scored. If status is not ok, say why. " + _SUB_RULES,
         "tools": [get_interior]},
        {"name": "stakes-analyst",
         "description": "What is at stake for one building_id: CoStar tenants, known employees, occupied area, last sale "
                        "with price status, and embodied carbon at stake. Give it the building_id.",
         "system_prompt": "You are the stakes analyst. Call get_stakes once for the building_id you are given and summarise "
                          "tenant count, largest tenants, known employees (and how many tenants that covers), occupied sq ft, "
                          "last sale with its price status, and embodied_carbon_at_stake with its caveats (US commercial "
                          "average, A1-A3 only, not a forecast). " + _SUB_RULES,
         "tools": [get_stakes], "model": LIGHT_MODEL},
    ]

SYSTEM_TEAM = """You are Ember, the orchestrator of a hazard-readiness team for commercial buildings.
{hazard_prompt}
Process: (1) write a plan with the todo tool; (2) delegate with the task tool: in ONE turn issue three task calls in
parallel to exterior-analyst, interior-inspector and stakes-analyst, each told the building_id; (3) call score_risk
(it reads what the specialists gathered); (4) write the report; update todo statuses as you go.
The score comes only from score_risk: report its number, band and factors exactly. Never compute or adjust a score.
Report format: (1) exposure score 0-100 labelled ESTIMATE (higher = more exposed, lower is better; never call it a "readiness"
score) with 3-5 reasons citing score_risk's evidence strings; (2) fix list
from score_risk's ranked_fixes, points_removed written as "-N points" (a reduction, never "+N"), relative cost and relative carbon tag (our
heuristic, not sourced; call a high-carbon fix removing 3 or fewer points "low payoff for its carbon", mention any
tradeoff text), no dollar figures unless the user gave a budget; (3) what is at stake, from the stakes-analyst (not recorded = say so;
carbon at stake is upfront A1-A3 carbon a rebuild would re-emit, US commercial average, no probability of loss, never
"avoided"); (4) interior, from the interior-inspector, as uncertain image readings; (5) resilience: 2-3 sentences on
why the top fixes matter for sustainability and climate adaptation (protecting an existing building and its tenants avoids
loss, displacement and rebuilding), qualitative, no figures beyond what tools returned, say "not quantified" for
benefits; (6) data gaps: score_risk's `missing` factors, hazard zone problems, mock data if any.
Follow-ups: what_if for "what if I do X", plan_budget for budgets (ask for per-fix dollar costs; never invent prices),
draft_action to save a draft. Quote results exactly; keep answers short.
Rules: the score is a transparent heuristic, not a validated model. Never invent data a specialist or tool did not
return. Do not state distances for defensible-space zones; do not interpret the roof condition value. Images are shown
by the app; you cannot open image files."""

TEAM_TOOLS = [score_risk, what_if, plan_budget, draft_action]

def build_agent(hazard: str, memory: bool = False, team: bool = True):
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
        kw["interrupt_on"] = {"draft_action": True}  # human approves/edits/rejects drafts before they are saved
    if team:
        return create_deep_agent(model=LLM_MODEL, tools=TEAM_TOOLS, subagents=_subagents(),
                                 middleware=[TodoListMiddleware()],
                                 system_prompt=SYSTEM_TEAM.format(hazard_prompt=HAZARDS[hazard]["prompt"]), **kw)
    return create_deep_agent(model=LLM_MODEL, tools=TOOLS, middleware=[TodoListMiddleware()],
                             system_prompt=SYSTEM_SOLO.format(hazard_prompt=HAZARDS[hazard]["prompt"]), **kw)

_LANE_BY_TOOL = {"get_stakes": "stakes-analyst", "get_interior": "interior-inspector", "get_property": "exterior-analyst",
                 "get_parcel_attributes": "exterior-analyst", "get_aerial": "exterior-analyst",
                 "get_hazard_zone": "exterior-analyst", "detect_hazard_features": "exterior-analyst"}


def _events(agent, inp, cfg):
    """Yield UI events: plan | tool_call | tool_result | text | usage | interrupt, tagged with `lane`."""
    lane_of = {}  # subgraph namespace -> subagent name, learned from the first tool it calls
    for ns, chunk in agent.stream(inp, config=cfg, stream_mode="updates", subgraphs=True):
        lane = "orchestrator"
        if ns:
            lane = lane_of.get(tuple(ns), "subagent")
        for _node, upd in (chunk or {}).items():
            if _node == "__interrupt__":
                for it in upd:
                    v = getattr(it, "value", it) or {}
                    yield {"type": "interrupt", "requests": v.get("action_requests", []) if isinstance(v, dict) else []}
                continue
            if not isinstance(upd, dict):
                continue
            if upd.get("todos") and not ns:
                yield {"type": "plan", "todos": upd["todos"]}
            for m in upd.get("messages", []) or []:
                kind = getattr(m, "type", "")
                if kind == "ai":
                    u = getattr(m, "usage_metadata", None)
                    for tc in getattr(m, "tool_calls", []) or []:
                        if ns and tuple(ns) not in lane_of and tc["name"] in _LANE_BY_TOOL:
                            lane_of[tuple(ns)] = lane = _LANE_BY_TOOL[tc["name"]]
                        yield {"type": "tool_call", "name": tc["name"], "args": tc["args"], "lane": lane}
                    if u:
                        yield {"type": "usage", "lane": lane, "input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0)}
                    txt = m.content if isinstance(m.content, str) else "".join(
                        b.get("text", "") for b in m.content if isinstance(b, dict))
                    if txt.strip() and not getattr(m, "tool_calls", None) and not ns:
                        yield {"type": "text", "text": txt, "lane": lane}
                elif kind == "tool":
                    yield {"type": "tool_result", "name": m.name, "lane": lane,
                           "content": str(m.content)[:20000 if m.name in ("detect_hazard_features", "get_interior") else 2000]}


def stream_events(agent, building_id: int, hazard: str, user_msg: str | None = None, thread_id: str | None = None):
    msg = user_msg or f"Assess {hazard} readiness for building_id={building_id}."
    cfg = {"configurable": {"thread_id": thread_id}} if thread_id else None
    yield from _events(agent, {"messages": [{"role": "user", "content": msg}]}, cfg)


def resume_events(agent, decisions: list[dict], thread_id: str):
    """Continue after an approval gate. decisions: [{'type': 'approve'|'reject'} or an edit] in request order."""
    from langgraph.types import Command
    yield from _events(agent, Command(resume={"decisions": decisions}), {"configurable": {"thread_id": thread_id}})
