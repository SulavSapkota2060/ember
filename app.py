import json, os, threading, time, uuid
import altair as alt
import streamlit as st
from config import MOCK, LLM_MODEL, OUT_DIR
from hazards import HAZARDS
from tools import costar
import scoring
import sustainability
import portfolio
import equity
import agent as ag

st.set_page_config(page_title="Ember", layout="wide")
S = st.session_state
LANE_ICON = {"orchestrator": "🧭", "exterior-analyst": "🛰️", "interior-inspector": "🏢", "stakes-analyst": "💼",
             "reviewer": "🔎", "subagent": "🤖"}
QUICK = {  # one-click agent jobs (no typing); each is sent to the same agent thread as a follow-up
    "Plan my retrofit budget": "I have $5,000. Use these costs: screen vents $800, clear zone 1 $2,500, thin zone 2 $1,500, "
                               "clear roof debris $400, clear dry brush $600. What should I do first? Use plan_budget.",
    "Brief for the insurer": "Rewrite the findings as a one-page briefing for the building's property insurer. Use only "
                             "data already gathered; keep the estimate and data-gap caveats.",
    "Brief for tenants": "Rewrite the findings as a short, calm notice to tenants: what we found, what is being done, "
                         "what is not known. Use only data already gathered.",
    "Draft contractor email": "Draft a contractor request email for the cheapest fix with a real score effect, using the "
                              "draft_action tool.",
}
STRESS_LLM = ("This is a TEST with planted errors. First call score_risk. Then write a SHORT draft report (5 lines) containing "
              "these deliberate errors: exposure score 41, vegetation zone 1 tree cover 63%, and call the carbon at stake "
              "'avoided'. Send that draft to the reviewer with one task call, then give the corrected short report and the "
              "Reviewer line.")


def nr(v):
    return "not recorded" if v is None else (f"{v:,.0f}" if isinstance(v, float) else str(v))


def plan_md(todos):
    return "\n".join(f"- {'✅' if t.get('status')=='completed' else '🔄' if t.get('status')=='in_progress' else '⬜'} {t.get('content')}"
                     for t in todos)


def note_event(r, ev):
    """Keep the agents' visible work for the Agent team tab: reasoning summaries, messages, tool calls and results."""
    if ev["type"] in ("thinking", "say", "tool_call", "tool_result"):
        if ev["type"] == "tool_call" and ev["name"] == "write_todos":
            return
        r["events"].append({"t": round(time.time() - r["t0"], 1), **ev})
    elif ev["type"] == "usage":
        lt = r["lane_tokens"].setdefault(ev["lane"], [0, 0])
        lt[0] += ev["input"]; lt[1] += ev["output"]


BAND_COLOR = {"low": "#2e9e5b", "moderate": "#d99a1c", "high": "#d64545"}
CARBON_COLOR = {"low": "#2e9e5b", "med": "#d99a1c", "high": "#d64545"}


def badge(text, bg):
    return (f'<span style="background:{bg};color:#fff;padding:3px 11px;border-radius:12px;font-size:0.85rem;'
            f'margin:0 6px 6px 0;display:inline-block">{text}</span>')


def gauge(score, base=None):
    """0-100 bar split into the three bands, with a marker at the score (and a ghost marker at the pre-fix score)."""
    import pandas as pd
    bands = pd.DataFrame({"a": [0, 33, 66], "b": [33, 66, 100], "c": [BAND_COLOR["low"], BAND_COLOR["moderate"], BAND_COLOR["high"]]})
    layers = [alt.Chart(bands).mark_bar(height=16, opacity=0.85).encode(
        x=alt.X("a:Q", scale=alt.Scale(domain=[0, 100]), axis=alt.Axis(values=[0, 33, 66, 100], title=None, labelColor="#9aa0a6")),
        x2="b:Q", color=alt.Color("c:N", scale=None))]
    if base is not None and base != score:
        layers.append(alt.Chart(pd.DataFrame({"s": [base]})).mark_rule(color="#9aa0a6", strokeDash=[3, 3], strokeWidth=2).encode(x="s:Q"))
    layers.append(alt.Chart(pd.DataFrame({"s": [score]})).mark_rule(color="#ffffff", strokeWidth=5).encode(x="s:Q"))
    return alt.layer(*layers).properties(height=44)


def fix_help(r):
    return f"{r['label']}. Cost {r['cost']}, carbon {r['carbon']} (our heuristic, not sourced)." + (f" Tradeoff: {r['tradeoff']}." if r.get("tradeoff") else "")


def summary_card(bid, facts, ranked):
    """The report at a glance: three icon panels and a chart of what moves the score. Text lives in the expander below."""
    import pandas as pd
    stk = costar.get_stakes(bid)
    cs = sustainability.carbon_at_stake(costar.get_property(bid).get("gba_sqft"))
    q = ag.get_equity(bid)
    inner = ag.get_view(bid)["interior"]
    z = facts.get("zone_hazard") or {}
    c1, c2, c3 = st.columns(3)
    with c1.container(border=True):
        st.markdown("**🛰️ Outside**")
        st.markdown(f"**{z['class'] + ' fire zone' if z.get('in_zone') else 'Outside mapped fire zone'}**" if z.get("status") == "ok" else "**Zone unavailable**")
        st.caption(f"Tree cover, zone 1: {facts.get('z1t')}%  \nRoof: {facts.get('roof_material') or 'unknown'}"
                   + (f"  \n{facts.get('roof_vents')} unscreened roof vents" if facts.get("roof_vents") else ""))
    with c2.container(border=True):
        st.markdown("**🏢 Inside**")
        if inner.get("status") == "ok":
            obs = inner["observations"]
            seen = [k.replace("_", " ") for k, o in obs.items() if o["status"] == "seen"]
            tell = [k.replace("_", " ") for k, o in obs.items() if o["status"] == "cannot_tell"]
            st.markdown(f"**{len(seen)} of {len(obs)} items confirmed**")
            st.caption(f"Seen: {', '.join(seen) or 'none'}  \nCan't tell: {', '.join(tell) or 'none'}")
        else:
            st.markdown("**Interior unavailable**")
    with c3.container(border=True):
        st.markdown("**👥 People and carbon**")
        emp = stk["employees_known"]
        st.markdown(f"**{stk['tenant_count']} tenants" + (f" · {emp:,}+ employees**" if emp else "**"))
        st.caption((f"{cs['tonnes_co2e']:,} t CO2e at stake  \n" if cs else "") +
                   (f"Neighborhood burden: {equity.ordinal(q['burden_percentile'])} pct" if q.get("status") == "ok" else "Neighborhood burden: unavailable"))
    if ranked:
        top = pd.DataFrame([{"fix": r["short"], "points removed": r["points_removed"], "carbon of the work": r["carbon"]} for r in ranked[:5]])
        chart = alt.Chart(top).mark_bar().encode(
            y=alt.Y("fix:N", sort="-x", title=None),
            x=alt.X("points removed:Q", title="Exposure points removed by this fix alone"),
            color=alt.Color("carbon of the work:N", scale=alt.Scale(domain=["low", "med", "high"], range=[CARBON_COLOR["low"], CARBON_COLOR["med"], CARBON_COLOR["high"]]),
                            legend=alt.Legend(orient="bottom", title="Carbon of doing the work (our heuristic)")),
            tooltip=["fix", "points removed", "carbon of the work"]).properties(height=34 * len(top) + 30)
        st.altair_chart(chart, width="stretch")



NODE_COLOR = {"orchestrator": "#3b6fd4", "exterior-analyst": "#2e8b8b", "interior-inspector": "#8e5bd4", "stakes-analyst": "#c9791f",
              "reviewer": "#c0392b"}


def flow_dot(run):
    """Who talked to whom, with timing, verdicts and tokens, as a graph. Built only from the real handoffs of this run."""
    hs = run.get("handoffs", [])[:14]
    names = []
    for h in hs:
        for n in (h["from"], h["to"]):
            if n not in names:
                names.append(n)
    tok = run.get("lane_tokens", {})
    verdict = {}
    for h in hs:
        if h["from"] == "reviewer":
            try:
                rv = json.loads(h["text"][h["text"].index("{"): h["text"].rindex("}") + 1])
                verdict["reviewer"] = (("✓ pass" if rv.get("verdict") == "pass" else "! revise") +
                                       f" · {rv.get('numbers_checked', '?')} numbers" + (f" · {len(rv['issues'])} issues" if rv.get("issues") else ""))
            except Exception:
                pass
    lines = ['digraph G {', 'rankdir=LR; bgcolor="transparent"; nodesep=0.35; ranksep=0.9;',
             'node [shape=box, style="rounded,filled", fontname="Helvetica", fontcolor="white", fontsize=12, margin="0.18,0.1"];',
             'edge [color="#9aa0a6", fontcolor="#cfd3d7", fontname="Helvetica", fontsize=10];']
    for n in names:
        extra = []
        if n in verdict:
            extra.append(verdict[n])
        if n in tok:
            extra.append(f"{(tok[n][0] + tok[n][1]) / 1000:.1f}k tokens")
        label = n + ("\\n" + " · ".join(extra) if extra else "")
        lines.append(f'"{n}" [label="{label}", fillcolor="{NODE_COLOR.get(n, "#5d6d7e")}"];')
    for h in hs:
        kind = "task" if h["from"] == "orchestrator" else "report"
        t = f" @ {h['t']}s" if h.get("t") is not None else ""
        lines.append(f'"{h["from"]}" -> "{h["to"]}" [label="{kind}{t}"];')
    lines.append("}")
    return "\n".join(lines)



def line_for(ev):
    return f"{LANE_ICON.get(ev['lane'], '🤖')} **{ev['lane']}** · `{ev['name']}` {json.dumps(ev['args'])[:100]}"


# ---------------- sidebar ----------------
city_default, multi_default = "Los Angeles", False
_blds_all = [b for b in costar.list_buildings(city_default, multi_default) if b["matterport"]]
if S.get("_goto"):  # set by the portfolio view; must be applied before the widgets below exist
    _b = next((b for b in _blds_all if b["id"] == S["_goto"]), None)
    if _b:
        S["bld_pick"], S["view"] = _b, "Building"
    S["_goto"] = None

with st.sidebar:
    st.title("Ember")
    st.caption("Fire exposure for commercial buildings (lower is better). Scores are heuristic ESTIMATES, not a validated model.")
    view_mode = st.radio("View", ["Building", "Portfolio"], key="view", horizontal=True)
    hazard = st.selectbox("Hazard", list(HAZARDS), format_func=lambda k: HAZARDS[k]["label"])
    city = st.selectbox("City", ["Los Angeles", "Houston"])
    multi = st.checkbox("Include apartments", False)
    blds = [b for b in costar.list_buildings(city, multi) if b["matterport"]]
    sel = st.selectbox("Building", blds, key="bld_pick", format_func=lambda b: f"{b['name']} ({b['type']}) #{b['id']}")
    st.write("Vexcel: **MOCK**" if MOCK else "Vexcel: live")
    if not LLM_MODEL:
        st.warning("LLM_MODEL not set in .env")
    deep = st.checkbox("Deep mode (LLM subagents + LLM reviewer, slower)", False,
                       help="Default 'fast' mode runs the specialist workers in parallel and writes one short report; a code "
                            "reviewer checks every number. Deep mode uses separate LLM subagents and an LLM reviewer (about "
                            "50s).")
    go = st.button("Investigate", type="primary", disabled=not (sel and LLM_MODEL) or view_mode == "Portfolio", width="stretch")

if view_mode == "Portfolio":
    st.subheader("Portfolio: where would retrofit money protect the most?")
    rows = portfolio.load()
    if not rows:
        st.info("Run `python scripts/build_portfolio.py` first.")
        st.stop()
    st.caption("Each dot is an LA building with a Matterport model. **Screening** exposure uses only the outside data "
               "(tree cover by zone, roof material/discoloration), CoStar sprinklers/construction and the CAL FIRE zone. It has "
               "no detections, roof-defect flags, vent counts or interior, so it differs from a full investigation (on the 4 "
               "buildings compared it ran 1-9 points lower). Carbon is upfront embodied carbon at stake (CLF averages x CoStar "
               "area), not a forecast. Dashed lines are portfolio medians, not danger thresholds.")
    import pandas as pd
    df = pd.DataFrame(rows).dropna(subset=["carbon_t"])
    df["people"] = df["employees_known"].fillna(0) + 5
    mx, my = float(df["screening_score"].median()), float(df["carbon_t"].median())
    color_by = st.radio("Colour dots by", ["Neighborhood burden (CalEnviroScreen)", "CAL FIRE zone"], horizontal=True)
    by_burden = color_by.startswith("Neighborhood")
    pick = alt.selection_point(name="pick", fields=["id"])
    base = alt.Chart(df)
    dots = base.mark_circle(opacity=0.75).encode(
        x=alt.X("screening_score:Q", title="Screening exposure (0-100, lower is better)"),
        y=alt.Y("carbon_t:Q", scale=alt.Scale(type="log"), title="Embodied carbon at stake (t CO2e, log scale)"),
        size=alt.Size("people:Q", legend=None, scale=alt.Scale(range=[40, 700])),
        color=(alt.Color("ces_pctile:Q", title="Neighborhood burden percentile", scale=alt.Scale(scheme="orangered", domain=[0, 100]))
               if by_burden else
               alt.Color("zone:N", title="CAL FIRE zone", scale=alt.Scale(domain=["Very High", "High", "Moderate", "none", "unknown"],
                         range=["#d62728", "#ff7f0e", "#f2c200", "#8a8f98", "#cccccc"]))),
        tooltip=["name", "type", "screening_score", "band", "zone", "ces_pctile", "carbon_t", "employees_known", "tenants"],
        opacity=alt.condition(pick, alt.value(0.9), alt.value(0.55))).add_params(pick)
    vline = base.mark_rule(strokeDash=[4, 4], color="#888").encode(x=alt.datum(mx))
    hline = base.mark_rule(strokeDash=[4, 4], color="#888").encode(y=alt.datum(my))
    ev = st.altair_chart(dots + vline + hline, on_select="rerun", selection_mode="pick", height=460)
    st.caption("Dot size = known employees (CoStar; unknown shown smallest). Top-right = above both medians: high exposure and "
               "high carbon at stake. Click a dot to select it.")
    hi = df[df["ces_pctile"] >= 75]
    both = hi[(hi["screening_score"] > mx) & (hi["carbon_t"] > my)]
    st.markdown(f"**Equity lens:** {len(hi)} of {len(df)} buildings sit in the most-burdened quarter of California census tracts "
                f"(CalEnviroScreen percentile 75 or higher); {len(both)} of them are also above both medians.")
    chosen_ids = [p["id"] for p in (ev.selection.get("pick") or [])] if ev and getattr(ev, "selection", None) else []
    if chosen_ids:
        r = df[df["id"] == chosen_ids[0]].iloc[0]
        st.markdown(f"**{r['name']}** · {r['type']} · screening exposure {r['screening_score']} ({r['band']}) · zone: {r['zone']} · "
                    f"{r['carbon_t']:,.0f} t CO2e at stake · employees known: {nr(r['employees_known'])}")
        if st.button("Open in Building view and investigate", type="primary"):
            S["_goto"] = int(r["id"])
            st.rerun()
    top = df[(df["screening_score"] > mx) & (df["carbon_t"] > my)].sort_values("screening_score", ascending=False)
    st.markdown(f"#### Above both medians ({len(top)} of {len(df)})")
    st.dataframe(top[["name", "type", "screening_score", "zone", "ces_pctile", "carbon_t", "employees_known", "tenants"]]
                 .rename(columns={"screening_score": "screening exposure", "carbon_t": "carbon at stake (t)",
                                  "ces_pctile": "neighborhood burden pctile"}),
                 hide_index=True, width="stretch")
    st.stop()

if not sel:
    st.stop()


@st.cache_resource
def _warm_start():
    """Once per server start: load the big CoStar files and prepare the demo buildings in the background."""
    def job():
        for f in (costar._props, costar._models, costar._occupancies, costar._companies, costar._sales):
            f()
        for i in os.environ.get("DEMO_BUILDING_IDS", "248351,249226").split(","):
            if i.strip():
                ag.prefetch_async(int(i), "wildfire")
    threading.Thread(target=job, daemon=True).start()
    return True


_warm_start()
ag.prefetch_async(sel["id"], hazard)  # starts gathering this building's data while the user reads the stakes (idempotent)

run = S.get("run")
if run and run["bid"] != sel["id"]:
    run = None  # a different building is selected: show it fresh


def start_run():
    r = {"bid": sel["id"], "hazard": hazard, "tid": uuid.uuid4().hex, "lanes": {}, "plan": [], "report": "",
         "chat": [], "tokens": [0, 0], "pending": None, "apply": [], "handoffs": [], "deep": deep, "seconds": None, "events": [], "lane_tokens": {}}
    t0 = time.time()
    r["t0"] = t0
    r["agent"] = ag.build_agent(hazard, memory=True, mode="deep" if deep else "fast")
    with st.status("Agent team working...", expanded=True) as status:
        plan_box, log_box, text_box = st.empty(), st.container(), st.empty()
        acc = ""
        for ev in ag.stream_events(r["agent"], sel["id"], hazard, thread_id=r["tid"]):
            note_event(r, ev)
            if ev["type"] == "plan":
                r["plan"] = ev["todos"]
                plan_box.markdown("**Plan**\n" + plan_md(ev["todos"]))
            elif ev["type"] == "tool_call" and ev["name"] != "write_todos":
                r["lanes"].setdefault(ev["lane"], []).append(line_for(ev))
                log_box.markdown(line_for(ev))
            elif ev["type"] == "thinking":
                log_box.markdown(f"💭 **{ev['lane']}** · reasoning summary: _{ev['text'][:220].strip()}..._")
            elif ev["type"] == "handoff":
                r["handoffs"].append({**ev, "t": round(time.time() - t0, 1)})
                log_box.markdown(f"{LANE_ICON.get(ev['from'], '🤖')} → {LANE_ICON.get(ev['to'], '🤖')} "
                                 f"**{ev['from']} → {ev['to']}**")
            elif ev["type"] == "text_delta":
                acc += ev["text"]
                text_box.markdown(acc)
            elif ev["type"] == "usage":
                r["tokens"][0] += ev["input"]; r["tokens"][1] += ev["output"]
            elif ev["type"] == "text":
                r["report"] = ev["text"]
        r["plan"] = [dict(t, status="completed") for t in r["plan"]]
        if not deep and r["report"]:  # instant, no-LLM review: every number must be in the evidence
            rv = ag.code_review(sel["id"], r["report"])
            r["handoffs"].append({"from": "reviewer", "to": "orchestrator", "text": json.dumps(rv), "t": round(time.time() - t0, 1)})
        r["seconds"] = round(time.time() - t0, 1)
        status.update(label=f"Done in {r['seconds']}s", state="complete", expanded=False)
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / f"report_{sel['id']}.md").write_text(r["report"])
    return r


if go:
    try:
        S["run"] = run = start_run()
        st.rerun()
    except Exception as e:
        st.error(f"{type(e).__name__}: {e}")

# agent actions that tick the checkboxes must be applied BEFORE the widgets are created
if run and run["apply"]:
    for fid in run["apply"]:
        S[f"fix_{run['bid']}_{fid}"] = True
    run["apply"] = []

rail, main = st.columns([1, 2], gap="large")

# ---------------- left rail: score + what-if (always visible) ----------------
with rail:
    if not run:
        st.info("Pick a building and click **Investigate**. The score, fixes and budget planner appear here.")
    else:
        bid = run["bid"]
        facts = ag.get_facts(bid)
        base = scoring.score(facts)
        ranked = scoring.rank_fixes(facts)
        chosen = [r["id"] for r in ranked if S.get(f"fix_{bid}_{r['id']}")]
        after = scoring.score(scoring.apply_fixes(facts, chosen))
        st.metric("Fire exposure score (ESTIMATE, lower is better)", f"{after['score']} / 100",
                  help="0-100 where HIGHER means MORE exposed. Fixes lower the score. Heuristic estimate, not a validated model.",
                  delta=f"{after['score'] - base['score']} pts vs. now" if chosen else None, delta_color="inverse")
        st.altair_chart(gauge(after["score"], base["score"] if chosen else None), width="stretch")
        z = facts.get("zone_hazard") or {}
        if z.get("status") == "ok" and z.get("in_zone"):
            zone_b = badge(f"🔥 CAL FIRE: {z['class']}", "#c0392b")
        elif z.get("status") == "ok":
            zone_b = badge("Outside mapped fire zone", "#5d6d7e")
        else:
            zone_b = badge("Zone unavailable", "#5d6d7e")
        q = ag.get_equity(bid)
        if q.get("status") == "ok":
            p = q["burden_percentile"]
            hood_b = badge(f"🏘 Burden {equity.ordinal(p)} pct", "#c0392b" if p >= 75 else "#d68910" if p >= 50 else "#5d6d7e")
        else:
            hood_b = badge("Burden unavailable", "#5d6d7e")
        st.markdown(badge(f"Band: {after['band']}" + (f" (was {base['band']})" if chosen and base["band"] != after["band"] else ""),
                          BAND_COLOR[after["band"]]) + zone_b + hood_b, unsafe_allow_html=True)
        with st.expander("Factor breakdown"):
            for f in after["factors"]:
                st.caption(f"{f['label']}: {f['points']} pts")
                st.progress(min(max(f["value"], 0.0), 1.0))
            if after["missing"]:
                st.caption("Not scored: " + ", ".join(m["factor"] for m in after["missing"]))
        st.markdown("**What-if: tick fixes** (the agent can tick these too)")
        for r in ranked:
            st.checkbox(f"{r['short']} · −{r['points_removed']} pts", key=f"fix_{bid}_{r['id']}", help=fix_help(r))
        if not ranked:
            st.info("No modelled fix changes this building's score.")
        with st.expander("Carbon-smart ranking: exposure removed per carbon spent"):
            st.caption("Carbon tags are OUR heuristic (low/med/high relative footprint of the work), not sourced figures. "
                       "'Low payoff' = a high-carbon fix that removes 3 or fewer points.")
            cs_rows = scoring.carbon_smart(ranked)
            st.dataframe([{"Fix": x["label"], "Points removed": x["points_removed"], "Carbon": x["carbon"], "Cost": x["cost"],
                           "Points per carbon unit": x["per_carbon"],
                           "Note": ("low payoff for its carbon" if x["low_payoff"] else x["tradeoff"] or "")} for x in cs_rows],
                         hide_index=True, width="stretch")
        with st.expander("Budget planner (you supply the costs; nothing is invented)"):
            budget = st.number_input("Budget ($)", min_value=0, step=500, value=0, key=f"bud_{bid}")
            costs = {}
            for r in ranked:
                v = st.number_input(f"Your cost: {r['label']} ($, 0 = unknown)", min_value=0, step=100, value=0,
                                    key=f"cost_{bid}_{r['id']}")
                if v > 0:
                    costs[r["id"]] = v
            if budget > 0 and costs:
                p = scoring.plan_within_budget(facts, budget, costs)
                if p["fixes"]:
                    st.success(f"Do: {', '.join(scoring.FIXES[i][0] for i in p['fixes'])}. Cost ${p['cost']:,.0f}. "
                               f"Score {p['before']} → {p['after']}.")
                else:
                    st.warning("Nothing useful fits that budget with the costs given.")
                if p["skipped_no_cost"]:
                    st.caption("Not considered (no cost given): " + ", ".join(p["skipped_no_cost"]))

# ---------------- right: tabs ----------------
with main:
    st.subheader(f"{sel['name']} · {sel['type']} · #{sel['id']}")
    names = ["Report", "Stakes", "Outside", "Inside", "Agent team", "Ask"] if run else \
            ["Stakes", "Report", "Outside", "Inside", "Agent team", "Ask"]
    tabs = dict(zip(names, st.tabs(names)))
    t_stakes, t_rep, t_out, t_in, t_team, t_ask = (tabs[n] for n in ("Stakes", "Report", "Outside", "Inside", "Agent team", "Ask"))

    with t_stakes:
        st.map([{"lat": sel["lat"], "lon": sel["lon"]}], zoom=14, height=220)
        with st.spinner("Loading CoStar tenancy and sale data (first load is slow)..."):
            stk = costar.get_stakes(sel["id"])
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Current tenants", stk["tenant_count"])
        c2.metric("Occupied sq ft", f"{stk['occupied_sqft']:,.0f}" if stk["occupied_sqft"] else "not recorded")
        c3.metric("Employees (known)", stk["employees_known"] if stk["employees_known"] is not None else "not recorded",
                  help=f"CoStar has headcount for {stk['employees_known_for']}; the true total is higher or unknown.")
        sale = stk["last_sale"]
        if sale:
            c4.metric("Last sale", f"${sale['price']:,.0f}" if sale["price"] else "price not recorded",
                      help=f"{sale['date']}; price status: {sale['price_status']}" + ("; portfolio sale" if sale["portfolio_sale"] else ""))
            c4.caption(f"{sale['date']} · {sale['price_status']}" + (" · portfolio" if sale["portfolio_sale"] else ""))
        else:
            c4.metric("Last sale", "none in CoStar kit")
        cs = sustainability.carbon_at_stake(costar.get_property(sel["id"]).get("gba_sqft"))
        st.markdown("#### Sustainability: embodied carbon at stake")
        if cs:
            st.metric("Upfront embodied carbon a rebuild would re-emit", f"{cs['tonnes_co2e']:,} t CO2e")
            st.bar_chart(cs["by_scope_tonnes"], horizontal=True)
            st.caption(cs["caveats"] + f" Source: {cs['basis']}: [CLF]({cs['source_url']}).")
        else:
            st.info("Gross building area not recorded in CoStar; carbon at stake not computed.")
        st.markdown("#### Equity lens: who lives with the burden around this building")
        eq = equity.lookup(sel["lat"], sel["lon"])
        if eq.get("status") == "ok":
            import pandas as pd
            bars = pd.DataFrame({"measure": ["Overall burden", "Pollution burden", "Population vulnerability"],
                                 "percentile": [eq["burden_percentile"], eq["pollution_percentile"], eq["vulnerability_percentile"]]})
            chart = alt.Chart(bars).mark_bar().encode(
                y=alt.Y("measure:N", sort=None, title=None),
                x=alt.X("percentile:Q", scale=alt.Scale(domain=[0, 100]), title="Percentile among California census tracts (higher = more burdened)"),
                color=alt.Color("percentile:Q", scale=alt.Scale(scheme="orangered", domain=[0, 100]), legend=None),
                tooltip=["measure", "percentile"]).properties(height=130)
            st.altair_chart(chart, width="stretch")
            st.caption(f"Census tract {eq['tract']} · {eq['source']}. {equity.CAVEAT}")
        else:
            st.info(f"Neighborhood burden unavailable for this building ({eq.get('status')}). Not treated as zero.")
        if stk["tenants"]:
            st.dataframe([{"Tenant": t["name"], "Employees": nr(t["employees"]), "Sq ft": nr(t["sqft"]),
                           "Lease expires": nr(t["lease_expires"])} for t in stk["tenants"]],
                         hide_index=True, width="stretch")
        st.caption("Figures are as CoStar records them; 'not recorded' means CoStar has no value, not zero.")

    view = ag.get_view(run["bid"]) if run else None

    with t_out:
        if view and view["images"]:
            st.image(view["images"]["annotated"], caption="Annotated aerial (Vexcel)")
            cols = st.columns(4)
            for k, c in enumerate(view["images"].get("crops", [])):
                cols[k % 4].image(c["path"], caption=f"{c['n']}: {c['label']}")
        else:
            st.info("Run Investigate to see the aerial view.")

    with t_in:
        inner = view and view["interior"]
        if inner and inner.get("status") == "ok":
            st.caption("A cheap model (Haiku) reads these images, then a stronger model (Sonnet) must confirm every 'seen' item "
                       "(otherwise it is downgraded to cannot_tell). Observations are uncertain; 'not_seen' is not proof an "
                       "item is absent. The sprinkler reading is unreliable and not scored (CoStar is used for sprinklers).")
            cols = st.columns(3)
            for k, im in enumerate(inner["images"]):
                with cols[k % 3]:
                    st.image(im["path"], caption=im["caption"])
                    st.link_button("Open 3D walkthrough", im["view_url"])
            st.dataframe([{"Item": n.replace("_", " "), "Final": o["status"], "First pass": o["first_pass"],
                           "Second look": o["second_look"] or "-", "Note": o["note"]}
                          for n, o in inner["observations"].items()], hide_index=True, width="stretch")
        elif inner and inner.get("status") != "not_run":
            st.info(f"Interior step: {inner.get('status')} {inner.get('reason', '')}")
        else:
            st.info("Run Investigate to see the interior reading.")

    with t_rep:
        if run and run["report"]:
            if run.get("seconds"):
                st.caption(f"{'Deep' if run.get('deep') else 'Fast'} mode · {run['seconds']}s · "
                           f"{run['tokens'][0]:,} in / {run['tokens'][1]:,} out tokens")
            summary_card(run["bid"], facts, ranked)
            with st.expander("Read the full agent report"):
                st.markdown(run["report"])
                st.download_button("Download report (.md)", run["report"], file_name=f"report_{run['bid']}.md")
        else:
            st.info("The agent's report appears here.")

    with t_team:
        if run:
            if run.get("handoffs"):
                st.graphviz_chart(flow_dot(run), width="stretch")
                st.caption("Who talked to whom in this run: arrows are real messages, with the time each was sent. "
                           "Reviewer shows its verdict; every agent shows the tokens it used.")
            with st.expander("Messages between agents", expanded=False):
              for h in run.get("handoffs", []):
                  with st.chat_message(h["from"], avatar=LANE_ICON.get(h["from"], "🤖")):
                      st.markdown(f"**{h['from']} → {h['to']}**")
                      txt = h["text"]
                      if h["from"] == "reviewer":
                          try:
                              rv = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
                              verdict_txt = "✅ pass" if rv.get("verdict") == "pass" else "⚠️ revise"
                              st.markdown(f"{verdict_txt} · {rv.get('numbers_checked', '?')} numbers checked · "
                                          f"{len(rv.get('issues', []))} issue(s)")
                              for iss in rv.get("issues", []):
                                  st.markdown(f"- ~~{iss.get('claim', '')[:120]}~~  \n  {iss.get('problem', '')[:220]}")
                              txt = None
                          except Exception:
                              pass
                      if txt:
                          st.markdown(txt[:260] + ("..." if len(txt) > 260 else ""))
                          if len(txt) > 260:
                              with st.expander("Full message"):
                                  st.markdown(txt)
            evs = run.get("events", [])
            if evs:
                with st.expander(f"Agent activity ({len(evs)} steps)", expanded=bool(run.get("deep"))):
                    st.caption("What each agent did, in order. 'Reasoning summary' is the summary the model API returns of its own "
                               "reasoning (Deep mode only; not a complete trace). Messages and tool results are the real text.")
                    for e in evs:
                        who = f"{LANE_ICON.get(e['lane'], '🤖')} **{e['lane']}** · {e['t']}s"
                        if e["type"] == "thinking":
                            st.markdown(f"💭 {who} · reasoning summary")
                            st.markdown("> " + e["text"].strip().replace("\n", "\n> ")[:900])
                        elif e["type"] == "say":
                            st.markdown(f"💬 {who} · message")
                            st.markdown(e["text"][:500] + ("..." if len(e["text"]) > 500 else ""))
                        elif e["type"] == "tool_call":
                            st.markdown(f"🔧 {who} · calls `{e['name']}` {json.dumps(e['args'])[:140]}")
                        elif e["type"] == "tool_result":
                            st.markdown(f"📥 {who} · `{e['name']}` returned {len(e['content']):,} chars")
                            with st.expander("Show result"):
                                st.code(e["content"][:1500])
            if run.get("lane_tokens"):
                st.dataframe([{"Agent": k, "Tokens in": v[0], "Tokens out": v[1]} for k, v in run["lane_tokens"].items()],
                             hide_index=True, width="stretch")
            with st.expander("Plan and tool calls by agent"):
                st.markdown(plan_md(run["plan"]))
                for lane, lines in run["lanes"].items():
                    st.markdown(f"**{LANE_ICON.get(lane, '🤖')} {lane}**")
                    for line in lines:
                        st.markdown(line.split("·", 1)[1] if "·" in line else line)
            st.caption(f"Tokens so far: {run['tokens'][0]:,} in / {run['tokens'][1]:,} out (all agents).")
        else:
            st.info("The agents' messages to each other appear here after Investigate.")

    with t_ask:
        if not run:
            st.info("Investigate first, then ask follow-ups or use the one-click jobs.")
        else:
            def ask(prompt, decisions=None):
                """Send a follow-up (or resume after an approval) and render the answer."""
                if decisions is None:
                    run["chat"].append(("user", prompt))
                    st.chat_message("user").markdown(prompt)
                    events = ag.stream_events(run["agent"], run["bid"], run["hazard"], user_msg=prompt, thread_id=run["tid"])
                else:
                    events = ag.resume_events(run["agent"], decisions, run["tid"])
                with st.chat_message("assistant"):
                    ans = ""
                    with st.spinner("Thinking..."):
                        try:
                            for ev in events:
                                note_event(run, ev)
                                if ev["type"] == "text":
                                    ans = ev["text"]
                                elif ev["type"] == "handoff":
                                    run.setdefault("handoffs", []).append({**ev, "t": round(time.time() - run["t0"], 1)})
                                elif ev["type"] == "usage":
                                    run["tokens"][0] += ev["input"]; run["tokens"][1] += ev["output"]
                                elif ev["type"] == "interrupt":
                                    run["pending"] = ev["requests"]
                                elif ev["type"] == "tool_call" and ev["name"] == "what_if":
                                    run["apply"] += [i for i in (ev["args"] or {}).get("fix_ids", []) if i in scoring.FIXES]
                                elif ev["type"] == "tool_result" and ev["name"] == "plan_budget":
                                    try:
                                        run["apply"] += json.loads(ev["content"]).get("fixes", [])
                                    except Exception:
                                        pass
                        except Exception as e:
                            ans = f"{type(e).__name__}: {e}"
                    if ans:
                        st.markdown(ans)
                        run["chat"].append(("assistant", ans))
                if run["apply"] or run["pending"]:
                    st.rerun()

            st.markdown("**One-click jobs**")
            bcols = st.columns(len(QUICK) + 1)
            queued = None
            for col, (label, prompt) in zip(bcols, QUICK.items()):
                if col.button(label, key=f"q_{label}", width="stretch"):
                    queued = prompt
            if bcols[-1].button("Stress-test the reviewer", key="q_stress", width="stretch",
                                help="Plants errors on purpose to show the reviewer catching them. A test, not a real finding."):
                if run.get("deep"):
                    queued = STRESS_LLM
                else:  # instant, no LLM: alter the real report, then run the same code check
                    sc0 = scoring.score(ag.get_facts(run["bid"]))["score"]
                    planted = run["report"].replace(f"{sc0}/100", "41/100", 1) + " Zone 1 tree cover is 63%."
                    rv = ag.code_review(run["bid"], planted)
                    run["handoffs"] += [
                        {"from": "test harness", "to": "reviewer", "text": "PLANTED ERRORS (a test): score changed to 41/100 and "
                         "'zone 1 tree cover is 63%' added to the real report."},
                        {"from": "reviewer", "to": "test harness", "text": json.dumps(rv)}]
                    caught = [i["problem"] for i in rv["issues"]]
                    st.warning(f"TEST (planted errors): the code reviewer flagged {len(caught)} number(s): "
                               + "; ".join(caught) + ". It checks numbers only; wording errors need Deep mode.")

            if run["pending"]:
                st.warning("The agent wants to save this draft. Review it first.")
                for req in run["pending"]:
                    a = req.get("args", {})
                    subj = st.text_input("Subject", a.get("subject", ""), key="ap_subj")
                    body = st.text_area("Body", a.get("body", ""), height=200, key="ap_body")
                    b1, _, b3 = st.columns(3)
                    decision = None
                    if b1.button("Approve (as shown)", type="primary", key="ap_ok"):
                        edited = subj != a.get("subject", "") or body != a.get("body", "")
                        decision = ({"type": "edit", "edited_action": {"name": req["name"], "args": {**a, "subject": subj, "body": body}}}
                                    if edited else {"type": "approve"})
                    if b3.button("Reject", key="ap_no"):
                        decision = {"type": "reject", "message": "User rejected the draft."}
                    if decision:
                        run["pending"] = None
                        ask(None, decisions=[decision])

            for role, text in run["chat"]:
                st.chat_message(role).markdown(text)
            typed = st.chat_input("Ask a follow-up, e.g. 'what if I screen the vents and clear zone 1?'")
            if queued or typed:
                ask(queued or typed)
