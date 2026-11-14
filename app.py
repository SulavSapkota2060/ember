import json, uuid
import altair as alt
import streamlit as st
from config import MOCK, LLM_MODEL, OUT_DIR
from hazards import HAZARDS
from tools import costar
import scoring
import sustainability
import portfolio
import agent as ag

st.set_page_config(page_title="Ember", layout="wide")
S = st.session_state
LANE_ICON = {"orchestrator": "🧭", "exterior-analyst": "🛰️", "interior-inspector": "🏢", "stakes-analyst": "💼",
             "subagent": "🤖"}
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


def nr(v):
    return "not recorded" if v is None else (f"{v:,.0f}" if isinstance(v, float) else str(v))


def plan_md(todos):
    return "\n".join(f"- {'✅' if t.get('status')=='completed' else '🔄' if t.get('status')=='in_progress' else '⬜'} {t.get('content')}"
                     for t in todos)


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
    pick = alt.selection_point(name="pick", fields=["id"])
    base = alt.Chart(df)
    dots = base.mark_circle(opacity=0.75).encode(
        x=alt.X("screening_score:Q", title="Screening exposure (0-100, lower is better)"),
        y=alt.Y("carbon_t:Q", scale=alt.Scale(type="log"), title="Embodied carbon at stake (t CO2e, log scale)"),
        size=alt.Size("people:Q", legend=None, scale=alt.Scale(range=[40, 700])),
        color=alt.Color("zone:N", title="CAL FIRE zone", scale=alt.Scale(domain=["Very High", "High", "Moderate", "none", "unknown"],
                        range=["#d62728", "#ff7f0e", "#f2c200", "#8a8f98", "#cccccc"])),
        tooltip=["name", "type", "screening_score", "band", "zone", "carbon_t", "employees_known", "tenants"],
        opacity=alt.condition(pick, alt.value(0.9), alt.value(0.55))).add_params(pick)
    vline = base.mark_rule(strokeDash=[4, 4], color="#888").encode(x=alt.datum(mx))
    hline = base.mark_rule(strokeDash=[4, 4], color="#888").encode(y=alt.datum(my))
    ev = st.altair_chart(dots + vline + hline, on_select="rerun", selection_mode="pick", height=460)
    st.caption("Dot size = known employees (CoStar; unknown shown smallest). Top-right = above both medians: high exposure and "
               "high carbon at stake. Click a dot to select it.")
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
    st.dataframe(top[["name", "type", "screening_score", "zone", "carbon_t", "employees_known", "tenants"]]
                 .rename(columns={"screening_score": "screening exposure", "carbon_t": "carbon at stake (t)"}),
                 hide_index=True, width="stretch")
    st.stop()

if not sel:
    st.stop()

run = S.get("run")
if run and run["bid"] != sel["id"]:
    run = None  # a different building is selected: show it fresh


def start_run():
    r = {"bid": sel["id"], "hazard": hazard, "tid": uuid.uuid4().hex, "lanes": {}, "plan": [], "report": "",
         "chat": [], "tokens": [0, 0], "pending": None, "apply": []}
    r["agent"] = ag.build_agent(hazard, memory=True)
    with st.status("Agent team working...", expanded=True) as status:
        plan_box, log_box = st.empty(), st.container()
        for ev in ag.stream_events(r["agent"], sel["id"], hazard, thread_id=r["tid"]):
            if ev["type"] == "plan":
                r["plan"] = ev["todos"]
                plan_box.markdown("**Plan**\n" + plan_md(ev["todos"]))
            elif ev["type"] == "tool_call" and ev["name"] != "write_todos":
                r["lanes"].setdefault(ev["lane"], []).append(line_for(ev))
                log_box.markdown(line_for(ev))
            elif ev["type"] == "usage":
                r["tokens"][0] += ev["input"]; r["tokens"][1] += ev["output"]
            elif ev["type"] == "text":
                r["report"] = ev["text"]
        status.update(label="Done", state="complete", expanded=False)
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
        z = facts.get("zone_hazard") or {}
        if z.get("status") == "ok":
            zone_txt = f"CAL FIRE zone: **{z['class']}**" if z.get("in_zone") else "outside CAL FIRE mapped zones (older maps)"
        else:
            zone_txt = "CAL FIRE zone: unavailable"
        st.markdown(f"Band: **{after['band']}**" + (f" (now {base['score']}, {base['band']})" if chosen else "") + f"  \n{zone_txt}")
        with st.expander("Factor breakdown"):
            for f in after["factors"]:
                st.caption(f"{f['label']}: {f['points']} pts")
                st.progress(min(max(f["value"], 0.0), 1.0))
            if after["missing"]:
                st.caption("Not scored: " + ", ".join(m["factor"] for m in after["missing"]))
        st.markdown("**What-if: tick fixes** (the agent can tick these too)")
        for r in ranked:
            st.checkbox(f"{r['label']} · cost {r['cost']} · carbon {r['carbon']} · −{r['points_removed']} pts alone", key=f"fix_{bid}_{r['id']}")
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
    t_stakes, t_out, t_in, t_rep, t_team, t_ask = st.tabs(
        ["Stakes", "Outside", "Inside", "Report", "Agent team", "Ask"])

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
            st.caption("A vision model reads these images, then a second skeptical pass must confirm every 'seen' item "
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
            st.markdown(run["report"])
            st.download_button("Download report (.md)", run["report"], file_name=f"report_{run['bid']}.md")
        else:
            st.info("The agent's report appears here.")

    with t_team:
        if run:
            cols = st.columns(4)
            for col, lane in zip(cols, ["orchestrator", "exterior-analyst", "interior-inspector", "stakes-analyst"]):
                with col:
                    st.markdown(f"##### {LANE_ICON[lane]} {lane}")
                    for line in run["lanes"].get(lane, []):
                        st.markdown(line.split("·", 1)[1] if "·" in line else line)
            st.markdown("**Plan**\n" + plan_md(run["plan"]))
            st.caption(f"Tokens so far: {run['tokens'][0]:,} in / {run['tokens'][1]:,} out (all lanes).")
        else:
            st.info("Live lanes for the orchestrator and three specialist subagents appear after Investigate.")

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
                                if ev["type"] == "text":
                                    ans = ev["text"]
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
            bcols = st.columns(len(QUICK))
            queued = None
            for col, (label, prompt) in zip(bcols, QUICK.items()):
                if col.button(label, key=f"q_{label}", width="stretch"):
                    queued = prompt

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
