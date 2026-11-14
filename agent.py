import json, threading
from config import LLM_MODEL
from hazards import HAZARDS
import scoring, sustainability, review, llm, equity
from config import VISION_MODEL as VISION, VISION_FIRST_MODEL as VISION_FIRST
from tools import costar, vexcel, hazard_zone, actions, matterport, interior

import time
from concurrent.futures import ThreadPoolExecutor
_cache = {}
_locks, _locks_guard = {}, threading.Lock()
_pool = ThreadPoolExecutor(8)      # data workers (exterior, interior, stakes, zone) run here
_pre_pool = ThreadPoolExecutor(2)  # whole-building prefetch jobs (separate pool so nested submits cannot deadlock)
_prefetched = {}                   # building_id -> Future


def _key_lock(building_id, key):
    """One lock per (building, step): parallel workers for the same building never wait on each other or on other buildings."""
    with _locks_guard:
        return _locks.setdefault((int(building_id), key), threading.Lock())

_HAZARD = "wildfire"  # set by build_agent; tools read it so the LLM never passes it  # building_id -> {"prop":..., "elements":...}

def _ctx(building_id):
    building_id = int(building_id)
    with _key_lock(building_id, "ctx"):  # tools run in parallel threads; fetch each building once
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
    with _key_lock(building_id, "detect"):
        if c.get("detect_result"):
            return json.dumps(c["detect_result"])
        return _detect(c)

def _detect(c):
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
    with _key_lock(building_id, "interior"):
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
    out["neighborhood_burden"] = _equity(_ctx(building_id))  # CalEnviroScreen tract percentile: context, not a fire metric
    return json.dumps(out)

def _zone(c):
    """Official zone lookup, cached per building. Deterministic, so the score never depends on the LLM calling it."""
    with _key_lock(c["prop"]["id"], "zone"):
        if "zone" not in c:
            c["zone"] = hazard_zone.get_hazard_zone(c["prop"]["lat"], c["prop"]["lon"], _HAZARD)
    return c["zone"]

def _equity(c):
    """Neighborhood burden (CalEnviroScreen tract), cached per building. Context only; never part of the score."""
    with _key_lock(c["prop"]["id"], "equity"):
        if "equity" not in c:
            c["equity"] = equity.lookup(c["prop"]["lat"], c["prop"]["lon"])
    return c["equity"]

def get_equity(building_id) -> dict:
    return _equity(_ctx(building_id))

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

# ---- fast path: all data work in parallel (and ahead of time), one short report from the LLM ----
def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception as e:  # a failed worker is reported, never hidden or invented
        return json.dumps({"status": "error", "reason": f"{type(e).__name__}: {str(e)[:160]}"})

def _gather(building_id):
    """Run the four data workers in parallel. Each is idempotent, so calling this twice costs nothing."""
    bid = int(building_id)
    t0 = time.time()
    futs = [_pool.submit(_safe, detect_hazard_features, bid), _pool.submit(_safe, get_interior, bid),
            _pool.submit(_safe, get_stakes, bid), _pool.submit(_safe, lambda b: json.dumps(_zone(_ctx(b))), bid),
            _pool.submit(_safe, lambda b: json.dumps(_equity(_ctx(b))), bid)]
    for f in futs:
        f.result()
    _ctx(bid)["gather_seconds"] = round(time.time() - t0, 1)

def prefetch_async(building_id, hazard="wildfire"):
    """Start gathering a building's data in the background (e.g. as soon as it is selected). Idempotent."""
    global _HAZARD
    _HAZARD = hazard
    bid = int(building_id)
    with _locks_guard:
        if bid not in _prefetched:
            _prefetched[bid] = _pre_pool.submit(_gather, bid)
    return _prefetched[bid]

def _worker_reports(bid, f, sc, stk, cs, inner):
    """What each specialist worker found, in one line each, built from the real tool results."""
    z = f.get("zone_hazard") or {}
    zone = (f"CAL FIRE zone {z['class']}" if z.get("in_zone") else "outside CAL FIRE mapped zones") if z.get("status") == "ok" else "CAL FIRE zone unavailable"
    c = _ctx(bid)
    dets = (c.get("detect_result") or {}).get("detections") or []
    labels = {}
    for d in dets:
        labels[d["label"]] = labels.get(d["label"], 0) + 1
    ext = (f"Tree cover zones 1/2/3: {f.get('z1t')}/{f.get('z2t')}/{f.get('z3t')}%. Roof {f.get('roof_material')}, "
           f"{f.get('roof_vents', 0)} vents unscreened. {zone}. Detections: {', '.join(f'{n} {k}' for k, n in labels.items()) or 'none'}.")
    if inner.get("status") == "ok":
        obs = inner["observations"]
        seen = [k.replace("_", " ") for k, o in obs.items() if o["status"] == "seen"]
        down = [k.replace("_", " ") for k, o in obs.items() if o["first_pass"] == "seen" and o["status"] != "seen"]
        ints = (f"{inner['n_images']} Matterport images read ({_short(VISION_FIRST)} first pass, {_short(VISION)} second look). "
                f"Seen: {', '.join(seen) or 'none'}. Downgraded after second look: {', '.join(down) or 'none'}.")
    else:
        ints = f"Interior unavailable: {inner.get('status')} {inner.get('reason', '')}".strip()
    sale = stk.get("last_sale")
    stakes = (f"{stk['tenant_count']} tenants, {nr(stk['employees_known'])} employees known ({stk['employees_known_for']}), "
              f"{nr(stk['occupied_sqft'])} sq ft occupied; last sale "
              f"{('$' + format(sale['price'], ',.0f') + ' ' + sale['date'] + ' (' + sale['price_status'] + (', PORTFOLIO sale' if sale.get('portfolio_sale') else '') + ')') if sale and sale.get('price') else 'not recorded'}; "
              f"carbon at stake {cs['tonnes_co2e']:,} t CO2e." if cs else "carbon at stake not computed (area not recorded).")
    q = _equity(c)
    if q.get("status") == "ok":
        stakes += (f" Neighborhood burden {equity.ordinal(q['burden_percentile'])} percentile "
                   f"(pollution {equity.ordinal(q['pollution_percentile'])}, vulnerability {equity.ordinal(q['vulnerability_percentile'])}; "
                   f"CalEnviroScreen 4.0, context only).")
    return {"exterior-analyst": ext, "interior-inspector": ints, "stakes-analyst": stakes}

def nr(v):
    return "not recorded" if v is None else (f"{v:,.0f}" if isinstance(v, (int, float)) else str(v))

def _short(m):
    return str(m).split(":")[-1].replace("claude-", "")

def _brief(building_id) -> dict:
    """Compact, complete evidence for the report writer. Also leaves the full facts in the cache for the UI."""
    bid = int(building_id)
    c = _ctx(bid)
    c["facts"] = f = scoring.build_facts(c["prop"], c["el"]["attrs"], c.get("dets"), zone=_zone(c), interior=c.get("interior"))
    sc = scoring.score(f)
    fixes = scoring.carbon_smart(scoring.rank_fixes(f))
    stk = costar.get_stakes(bid)
    cs = sustainability.carbon_at_stake(c["prop"].get("gba_sqft"))
    inner = _interior_view(c)
    c["worker_reports"] = _worker_reports(bid, f, sc, stk, cs, inner)
    z = f.get("zone_hazard") or {}
    return {
        "building": {"name": c["prop"].get("name"), "address": c["prop"].get("address"), "type": c["prop"].get("type"),
                     "stories": c["prop"].get("stories"), "sprinklers_costar": c["prop"].get("sprinklers")},
        "exposure_score": sc["score"], "band": sc["band"],
        "top_factors": [{"factor": x["label"], "points": x["points"], "evidence": x["evidence"]} for x in sc["factors"][:5]],
        "missing_factors": [m["factor"] for m in sc["missing"]],
        "fixes": [{"fix": x["label"], "points_removed": x["points_removed"], "cost": x["cost"], "carbon": x["carbon"],
                   "low_payoff_for_carbon": x["low_payoff"], "tradeoff": x["tradeoff"]} for x in fixes[:5]],
        "hazard_zone": ({"class": z.get("class"), "map": z.get("area")} if z.get("in_zone") else
                        ("outside mapped zones (older maps, not proof of low hazard)" if z.get("status") == "ok" else "unavailable")),
        "stakes": {"tenants": stk["tenant_count"], "largest_tenants": [t["name"] for t in stk["tenants"][:3]],
                   "employees_known": stk["employees_known"], "employees_coverage": stk["employees_known_for"],
                   "occupied_sqft": stk["occupied_sqft"], "last_sale": stk["last_sale"]},
        "neighborhood_burden": ({"percentile": _equity(c).get("burden_percentile"), "pollution_percentile": _equity(c).get("pollution_percentile"),
                                 "vulnerability_percentile": _equity(c).get("vulnerability_percentile"), "source": "CalEnviroScreen 4.0, census tract",
                                 "caveat": "context only; not a fire metric; not part of the score"}
                                if _equity(c).get("status") == "ok" else "unavailable"),
        "carbon_at_stake_tonnes_co2e": cs["tonnes_co2e"] if cs else None,
        "carbon_note": "upfront A1-A3 a rebuild would re-emit; US commercial average; not a forecast; never 'avoided'",
        "interior": ({k: o["status"] for k, o in inner["observations"].items()} if inner.get("status") == "ok" else inner.get("status")),
        "interior_caveat": "image readings are uncertain; not_seen is not proof of absence; sprinkler reading not scored",
    }

def assess(building_id: int) -> str:
    """Run the whole assessment in one step: parallel specialist workers (exterior: Vexcel + CAL FIRE zone; interior:
    Matterport images read by a vision model with a second look; stakes: CoStar + carbon) and the scorer. Returns
    one compact evidence brief with the exposure score, top factors, ranked fixes, stakes and gaps. Quote it exactly."""
    bid = int(building_id)
    fut = _prefetched.get(bid)
    if fut is not None:
        fut.result()
    _gather(bid)  # no-op when prefetched
    return json.dumps(_brief(bid))

def code_review(building_id, text: str) -> dict:
    """Instant, no-LLM review of a report: every number must appear in the evidence. Same JSON shape as the reviewer agent."""
    r = review.check_numbers(text, _evidence(building_id))
    issues = [{"claim": u["context"], "problem": f"number {u['number']} not found in the evidence"} for u in r["unmatched"]]
    return {"verdict": "pass" if not issues else "revise", "numbers_checked": r["checked"], "issues": issues, "by": "code"}

def _evidence(building_id) -> dict:
    """Ground truth for the reviewer: the actual outputs of the data tools and the scorer for this building."""
    c = _ctx(building_id)
    f = get_facts(building_id)
    sc = scoring.score(f)
    stk = costar.get_stakes(building_id)
    stk["embodied_carbon_at_stake"] = sustainability.carbon_at_stake(c["prop"].get("gba_sqft"))
    det = (c.get("detect_result") or {}).get("detections") or []

    def slim(o):  # same parcel data the exterior analyst saw, minus long geometry strings
        if isinstance(o, dict):
            return {k: slim(v) for k, v in o.items() if "wkt" not in k and "geometry" not in k}
        return [slim(v) for v in o] if isinstance(o, list) else o
    return {"building_id": int(building_id), "property": c["prop"], "parcel_attributes": slim(c["el"]["attrs"]), "score": sc,
            "ranked_fixes": scoring.rank_fixes(f), "stakes": stk, "hazard_zone": _zone(c), "neighborhood_burden": _equity(c),
            "interior": _interior_view(c), "detections": det,
            "scoring_conventions": "score 0-100, HIGHER = MORE EXPOSED; points_removed are reductions written as -N"}

def get_evidence(building_id: int) -> str:
    """The ground-truth evidence bundle for a building: score and factor evidence strings, ranked fixes, stakes,
    carbon at stake, hazard zone, interior observations, detections. Use it to audit a draft report."""
    return json.dumps(_evidence(building_id))

def check_numbers(building_id: int, text: str) -> str:
    """Code check (no LLM): list every number in `text` that does NOT appear in the evidence bundle. Unmatched
    numbers may still be valid sums/differences of evidence numbers; judge those yourself."""
    return json.dumps(review.check_numbers(text, _evidence(building_id)))

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

def _subagents(think=False):
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
                          "last sale with its price status, embodied_carbon_at_stake with its caveats (US commercial "
                          "average, A1-A3 only, not a forecast), and neighborhood_burden (CalEnviroScreen percentile: context "
                          "only, not a fire metric, not part of the score). " + _SUB_RULES,
         "tools": [get_stakes], "model": llm.chat(LIGHT_MODEL, think=think)},
        {"name": "reviewer",
         "description": "Independent auditor of a DRAFT report. Send it the building_id and the full draft text; it checks "
                        "every claim against the ground-truth evidence and returns a JSON verdict with issues. It never "
                        "rewrites the report.",
         "system_prompt": "You are the independent reviewer. You get a building_id and a DRAFT report. You did not write it. "
                          "Call get_evidence, call check_numbers with the draft text, then audit: (a) every number and claim "
                          "is supported by the evidence (a correct sum or difference of evidence numbers is fine; say so); "
                          "(b) points removed are written as -N, never +N; (c) the score is called an exposure ESTIMATE, "
                          "higher = more exposed; (d) no distances or units for defensible-space zones; (e) carbon at stake "
                          "is never called avoided or saved; (f) not_seen is never stated as absent; (g) hazard-zone claims "
                          "match the evidence; (h) no dollar figures the user did not give. Reply with JSON only: "
                          '{"verdict": "pass|revise", "numbers_checked": N, "issues": [{"claim": "...", "problem": "...", '
                          '"evidence": "..."}]}. Do not rewrite the report. Only flag real problems you can point to in the evidence.',
         "tools": [get_evidence, check_numbers]},
    ]

SYSTEM_TEAM = """You are Ember, the orchestrator of a hazard-readiness team for commercial buildings.
{hazard_prompt}
Process: (1) write a plan with the todo tool; (2) delegate with the task tool: in ONE turn issue three task calls in
parallel to exterior-analyst, interior-inspector and stakes-analyst, each told the building_id; (3) call score_risk
(it reads what the specialists gathered); (4) write a DRAFT report and send it, in full, to the reviewer with one task
call (description: "building_id=N" then the draft text); do NOT output the draft as your answer; (5) if the reviewer's
verdict is "revise", correct every issue it raised (re-delegate or re-run score_risk only if an issue needs it), then
give the final report; if "pass", give the draft unchanged. End the final report with one line:
"Reviewer: <verdict>, <numbers_checked> numbers checked, <k> issues fixed." Update todo statuses as you go.
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
FAST_TOOLS = [assess, what_if, plan_budget, draft_action]

SYSTEM_FAST = """You are Ember, a wildfire exposure analyst for commercial buildings.
{hazard_prompt}
Process: in ONE turn call write_todos (two items: "Gather evidence", "Write report") AND assess(building_id) together,
writing no text before the tools. Then write the report as your only text message. Never call write_todos again.
Everything comes from assess: quote its numbers and evidence strings exactly; never compute, round or adjust anything.
REPORT: 110 words maximum, markdown, exactly this shape:
**Exposure N/100 (band) · ESTIMATE; higher = more exposed, lower is better**
One short verdict sentence.
**Why:** 3 bullets quoting evidence strings.
**Fix first:** 3 bullets: fix · -N points · cost · carbon tag (our heuristic); say "low payoff for its carbon" when flagged and
name a tradeoff if present.
**At stake:** one line: tenants, known employees (with coverage), carbon at stake in tonnes CO2e (upfront A1-A3 a rebuild
would re-emit, US commercial average, not a forecast, never "avoided"), and the neighborhood burden percentile
(CalEnviroScreen; context only, not a fire metric, not part of the score); say "not recorded" for missing values.
**Gaps:** one line: missing factors and uncertain interior readings (not_seen is not proof of absence).
Rules: the score is a heuristic estimate; never call it a "readiness" score; no zone distances; do not interpret roof
condition; never invent data. Follow-ups: what_if for "what if I do X", plan_budget for budgets (ask for per-fix dollar
costs, never invent prices), draft_action to save a draft. Follow-up answers: 80 words maximum."""

def build_agent(hazard: str, memory: bool = False, mode: str = "fast"):
    """mode: 'fast' (default: parallel workers + one short report), 'deep' (LLM subagents + LLM reviewer, slower),
    'solo' (one agent, all tools)."""
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
    hp = HAZARDS[hazard]["prompt"]
    if mode == "deep":  # Deep mode shows the agents' work, including the API's summarized reasoning
        return create_deep_agent(model=llm.chat(LLM_MODEL, think=True), tools=TEAM_TOOLS, subagents=_subagents(think=True),
                                 middleware=[TodoListMiddleware()],
                                 system_prompt=SYSTEM_TEAM.format(hazard_prompt=hp), **kw)
    model = llm.chat(LLM_MODEL)
    if mode == "solo":
        return create_deep_agent(model=model, tools=TOOLS, middleware=[TodoListMiddleware()],
                                 system_prompt=SYSTEM_SOLO.format(hazard_prompt=hp), **kw)
    return create_deep_agent(model=model, tools=FAST_TOOLS, middleware=[TodoListMiddleware()],
                             system_prompt=SYSTEM_FAST.format(hazard_prompt=hp), **kw)

_LANE_BY_TOOL = {"get_evidence": "reviewer", "check_numbers": "reviewer", "get_stakes": "stakes-analyst",
                 "get_interior": "interior-inspector", "get_property": "exterior-analyst",
                 "get_parcel_attributes": "exterior-analyst", "get_aerial": "exterior-analyst",
                 "get_hazard_zone": "exterior-analyst", "detect_hazard_features": "exterior-analyst"}


def _events(agent, inp, cfg):
    """Yield UI events: plan | tool_call | tool_result | handoff | text_delta | text | usage | interrupt, tagged with `lane`."""
    lane_of = {}      # subgraph namespace -> subagent name, learned from the first tool it calls
    sub_of_call = {}  # orchestrator task tool_call id -> subagent name (to label the report that comes back)
    assess_bid = {}   # assess tool_call id -> building_id (its worker reports are read from the cache)
    for ns, mode, data in agent.stream(inp, config=cfg, stream_mode=["updates", "messages"], subgraphs=True):
        lane = "orchestrator"
        if ns:
            lane = lane_of.get(tuple(ns), "subagent")
        if mode == "messages":  # token stream of the orchestrator's own answer, for instant on-screen text
            ch = data[0]
            if not ns and type(ch).__name__ == "AIMessageChunk" and not getattr(ch, "tool_call_chunks", None):
                t = ch.content if isinstance(ch.content, str) else "".join(
                    b.get("text", "") for b in ch.content if isinstance(b, dict) and b.get("type") == "text")
                if t:
                    yield {"type": "text_delta", "text": t, "lane": lane}
            continue
        for _node, upd in (data or {}).items():
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
                    for tc in getattr(m, "tool_calls", []) or []:  # learn which subagent this is before labelling anything
                        if ns and tuple(ns) not in lane_of and tc["name"] in _LANE_BY_TOOL:
                            lane_of[tuple(ns)] = _LANE_BY_TOOL[tc["name"]]
                    if ns:
                        lane = lane_of.get(tuple(ns), "subagent")
                    blocks = m.content if isinstance(m.content, list) else []
                    for b in blocks:  # reasoning SUMMARY returned by the API (Deep mode only; empty otherwise)
                        if isinstance(b, dict) and b.get("type") == "thinking" and (b.get("thinking") or "").strip():
                            yield {"type": "thinking", "lane": lane, "text": b["thinking"]}
                    say = m.content if isinstance(m.content, str) else "".join(
                        b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
                    if say.strip() and (getattr(m, "tool_calls", None) or ns):  # intermediate message from an agent
                        yield {"type": "say", "lane": lane, "text": say}
                    for tc in getattr(m, "tool_calls", []) or []:
                        if tc["name"] == "task" and not ns:
                            who = (tc["args"] or {}).get("subagent_type", "subagent")
                            sub_of_call[tc["id"]] = who
                            yield {"type": "handoff", "from": "orchestrator", "to": who,
                                   "text": str((tc["args"] or {}).get("description", ""))}
                        if tc["name"] == "assess" and not ns:
                            assess_bid[tc["id"]] = int((tc["args"] or {}).get("building_id", 0))
                            for who in ("exterior-analyst", "interior-inspector", "stakes-analyst"):
                                yield {"type": "handoff", "from": "orchestrator", "to": who,
                                       "text": f"assess building_id={assess_bid[tc['id']]}: gather your data and report back"}
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
                    cid = getattr(m, "tool_call_id", "")
                    if m.name == "task" and not ns:
                        yield {"type": "handoff", "from": sub_of_call.get(cid, "subagent"), "to": "orchestrator", "text": str(m.content)}
                    if m.name == "assess" and cid in assess_bid:
                        for who, rep in (_cache.get(assess_bid[cid], {}).get("worker_reports") or {}).items():
                            yield {"type": "handoff", "from": who, "to": "orchestrator", "text": rep}
                    yield {"type": "tool_result", "name": m.name, "lane": lane,
                           "content": str(m.content)[:20000 if m.name in ("detect_hazard_features", "get_interior") else 2000]}


def stream_events(agent, building_id: int, hazard: str, user_msg: str | None = None, thread_id: str | None = None):
    msg = user_msg or f"Assess {hazard} exposure for building_id={building_id}."
    cfg = {"configurable": {"thread_id": thread_id}} if thread_id else None
    yield from _events(agent, {"messages": [{"role": "user", "content": msg}]}, cfg)


def resume_events(agent, decisions: list[dict], thread_id: str):
    """Continue after an approval gate. decisions: [{'type': 'approve'|'reject'} or an edit] in request order."""
    from langgraph.types import Command
    yield from _events(agent, Command(resume={"decisions": decisions}), {"configurable": {"thread_id": thread_id}})
