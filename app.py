import json
import streamlit as st
from config import MOCK, LLM_MODEL
from hazards import HAZARDS
from tools import costar
import agent as ag

st.set_page_config(page_title="Ember", layout="wide")
st.title("Ember: building hazard readiness")
st.caption("Risk scores are heuristic ESTIMATES, not a validated model.")

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
        c4.metric("Last sale", f"${sale['price']:,.0f}" if sale["price"] else "price not disclosed",
                  help=f"{sale['date']}; price status: {sale['price_status']}" + ("; portfolio sale" if sale["portfolio_sale"] else ""))
        c4.caption(f"{sale['date']} · {sale['price_status']}" + (" · portfolio" if sale["portfolio_sale"] else ""))
    else:
        c4.metric("Last sale", "none in CoStar kit")
    if stk["tenants"]:
        st.dataframe([{"Tenant": t["name"], "Employees": t["employees"], "Sq ft": t["sqft"], "Lease expires": t["lease_expires"]}
                      for t in stk["tenants"]], hide_index=True, use_container_width=True)
    st.caption("Figures are as CoStar records them; blanks mean CoStar has no value, not zero.")

if go:
    left, right = st.columns([1, 1])
    plan_box, log = left.empty(), left.container()
    report = right.container()
    try:
        agent = ag.build_agent(hazard)
        for ev in ag.stream_events(agent, sel["id"], hazard):
            if ev["type"] == "plan":
                plan_box.markdown("**Plan**\n" + "\n".join(
                    f"- {'✅' if t.get('status')=='completed' else '🔄' if t.get('status')=='in_progress' else '⬜'} {t.get('content')}"
                    for t in ev["todos"]))
            elif ev["type"] == "tool_call":
                log.markdown(f"🔧 `{ev['name']}` {json.dumps(ev['args'])[:200]}")
            elif ev["type"] == "tool_result":
                if ev["name"] == "detect_hazard_features":
                    try:
                        r = json.loads(ev["content"] if ev["content"].endswith("}") else "{}")
                        report.image(r["annotated"], caption="Annotated aerial")
                        for c in r["crops"]:
                            report.image(c["path"], caption=f"{c['n']}: {c['label']}", width=150)
                    except Exception:
                        pass
            elif ev["type"] == "text":
                report.markdown(ev["text"])
    except Exception as e:
        st.error(f"{type(e).__name__}: {e}")
