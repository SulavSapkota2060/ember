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
