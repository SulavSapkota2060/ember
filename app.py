import json, uuid
import streamlit as st
from config import MOCK, LLM_MODEL, OUT_DIR
from hazards import HAZARDS
from tools import costar
import scoring
import agent as ag

st.set_page_config(page_title="Ember", layout="wide")
st.title("Ember: building hazard readiness")
st.caption("Risk scores are heuristic ESTIMATES, not a validated model.")
S = st.session_state


def nr(v):
    return "not recorded" if v is None else (f"{v:,.0f}" if isinstance(v, float) else str(v))


def plan_md(todos):
    return "\n".join(f"- {'✅' if t.get('status')=='completed' else '🔄' if t.get('status')=='in_progress' else '⬜'} {t.get('content')}"
                     for t in todos)


with st.sidebar:
    hazard = st.selectbox("Hazard", list(HAZARDS), format_func=lambda k: HAZARDS[k]["label"])
    city = st.selectbox("City", ["Los Angeles", "Houston"])
    multi = st.checkbox("Include apartments", False)
    blds = [b for b in costar.list_buildings(city, multi) if b["matterport"]]
    sel = st.selectbox("Building", blds, format_func=lambda b: f"{b['name']} ({b['type']}) #{b['id']}")
    st.write("Vexcel: **MOCK**" if MOCK else "Vexcel: live")
    if not LLM_MODEL:
        st.warning("LLM_MODEL not set in .env")
    go = st.button("Investigate", type="primary", disabled=not (sel and LLM_MODEL))

if sel:
    st.map([{"lat": sel["lat"], "lon": sel["lon"]}], zoom=14, height=250)
    with st.spinner("Loading CoStar tenancy and sale data (first load is slow)..."):
        stk = costar.get_stakes(sel["id"])
    st.subheader("What's at stake (CoStar)")
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
    if stk["tenants"]:
        st.dataframe([{"Tenant": t["name"], "Employees": nr(t["employees"]), "Sq ft": nr(t["sqft"]),
                       "Lease expires": nr(t["lease_expires"])} for t in stk["tenants"]],
                     hide_index=True, width="stretch")
    st.caption("Figures are as CoStar records them; 'not recorded' means CoStar has no value, not zero.")

# ---- run the agent (live), then store results so the page can re-render without losing them ----
if go:
    run = {"bid": sel["id"], "hazard": hazard, "tid": uuid.uuid4().hex, "log": [], "plan": [],
           "images": None, "report": "", "chat": []}
    try:
        run["agent"] = ag.build_agent(hazard, memory=True)
        with st.status("Agent working...", expanded=True) as status:
            plan_box, log_box = st.empty(), st.container()
            for ev in ag.stream_events(run["agent"], sel["id"], hazard, thread_id=run["tid"]):
                if ev["type"] == "plan":
                    run["plan"] = ev["todos"]
                    plan_box.markdown("**Plan**\n" + plan_md(ev["todos"]))
                elif ev["type"] == "tool_call":
                    if ev["name"] != "write_todos":
                        run["log"].append(f"`{ev['name']}` {json.dumps(ev['args'])[:120]}")
                        log_box.markdown("🔧 " + run["log"][-1])
                elif ev["type"] == "tool_result" and ev["name"] == "get_interior":
                    try:
                        run["interior"] = json.loads(ev["content"])
                    except Exception:
                        pass
                elif ev["type"] == "tool_result" and ev["name"] == "detect_hazard_features":
                    try:
                        run["images"] = json.loads(ev["content"])
                    except Exception:
                        pass
                elif ev["type"] == "text":
                    run["report"] = ev["text"]
            status.update(label="Done", state="complete", expanded=False)
        OUT_DIR.mkdir(exist_ok=True)
        (OUT_DIR / f"report_{sel['id']}.md").write_text(run["report"])
        S["run"] = run
        st.rerun()
    except Exception as e:
        st.error(f"{type(e).__name__}: {e}")

run = S.get("run")
if sel and run and run["bid"] == sel["id"]:
    bid = run["bid"]
    facts = ag.get_facts(bid)
    base = scoring.score(facts)
    ranked = scoring.rank_fixes(facts)

    st.divider()
    left, right = st.columns([1, 2])
    with right:
        st.subheader("What-if: tick fixes to re-score")
        chosen = []
        for r in ranked:
            if st.checkbox(f"{r['label']} · cost {r['cost']} · −{r['points_removed']} pts alone", key=f"fix_{bid}_{r['id']}"):
                chosen.append(r["id"])
        if not ranked:
            st.info("No modelled fix changes this building's score.")
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
    after = scoring.score(scoring.apply_fixes(facts, chosen))
    with left:
        st.metric("Fire-readiness score (ESTIMATE)", f"{after['score']} / 100",
                  delta=f"{after['score'] - base['score']} pts vs. now" if chosen else None, delta_color="inverse")
        st.markdown(f"Band: **{after['band']}**" + (f" (now {base['score']}, {base['band']})" if chosen else ""))
        for f in after["factors"]:
            st.caption(f"{f['label']}: {f['points']} pts")
            st.progress(min(max(f["value"], 0.0), 1.0))
        if after["missing"]:
            st.caption("Not scored: " + ", ".join(m["factor"] for m in after["missing"]))

    img, rep = st.columns([1, 1])
    with img:
        if run["images"]:
            st.image(run["images"]["annotated"], caption="Annotated aerial (Vexcel)")
            for c in run["images"].get("crops", []):
                st.image(c["path"], caption=f"{c['n']}: {c['label']}", width=150)
    with rep:
        st.markdown(run["report"])
        st.caption(f"Saved to out/report_{bid}.md")

    inner = run.get("interior")
    if inner and inner.get("status") == "ok":
        st.subheader("Inside (Matterport)")
        st.caption("A vision model reads these images. Observations are uncertain; 'not_seen' is not proof an item is "
                   "absent. The sprinkler reading is unreliable and is not scored (CoStar is used for sprinklers).")
        cols = st.columns(3)
        for k, im in enumerate(inner["images"]):
            with cols[k % 3]:
                st.image(im["path"], caption=im["caption"])
                st.link_button("Open 3D walkthrough", im["view_url"])
        st.dataframe([{"Item": name.replace("_", " "), "Status": o["status"], "Note": o["note"]}
                      for name, o in inner["observations"].items()], hide_index=True, width="stretch")
    elif inner:
        st.info(f"Interior step: {inner.get('status')} {inner.get('reason', '')}")

    with st.expander("Agent plan and tool log"):
        st.markdown(plan_md(run["plan"]))
        for line in run["log"]:
            st.markdown("🔧 " + line)

    st.subheader("Ask the agent")
    for role, text in run["chat"]:
        st.chat_message(role).markdown(text)
    if prompt := st.chat_input("Ask a follow-up, e.g. 'I have $5,000, what first?'"):
        run["chat"].append(("user", prompt))
        st.chat_message("user").markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                ans = ""
                try:
                    for ev in ag.stream_events(run["agent"], bid, run["hazard"], user_msg=prompt, thread_id=run["tid"]):
                        if ev["type"] == "text":
                            ans = ev["text"]
                except Exception as e:
                    ans = f"{type(e).__name__}: {e}"
            st.markdown(ans)
        run["chat"].append(("assistant", ans))
