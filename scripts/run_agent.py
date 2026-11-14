"""End-to-end agent run without the UI. Usage: python scripts/run_agent.py BUILDING_ID [hazard]"""
import sys, time
sys.path.insert(0, ".")
import agent as ag
bid = int(sys.argv[1]); hazard = sys.argv[2] if len(sys.argv) > 2 else "wildfire"
a = ag.build_agent(hazard)
t = time.time(); inp = out = calls = 0
for chunk in a.stream({"messages": [{"role": "user", "content": f"Assess {hazard} readiness for building_id={bid}."}]}, stream_mode="updates"):
    for node, upd in (chunk or {}).items():
        if not isinstance(upd, dict): continue
        if upd.get("todos"):
            print("PLAN:", [(x.get("status"), x.get("content")[:50]) for x in upd["todos"]])
        for m in upd.get("messages", []) or []:
            u = getattr(m, "usage_metadata", None)
            if u: inp += u.get("input_tokens", 0); out += u.get("output_tokens", 0); calls += 1
            if m.type == "ai":
                for tc in m.tool_calls or []: print(f"[{time.time()-t:5.1f}s] CALL {tc['name']} {str(tc['args'])[:100]}")
                txt = m.content if isinstance(m.content, str) else "".join(b.get("text", "") for b in m.content if isinstance(b, dict))
                if txt.strip() and not m.tool_calls: print("\nFINAL:\n" + txt)
            elif m.type == "tool":
                print(f"[{time.time()-t:5.1f}s] RESULT {m.name}: {str(m.content)[:140]}")
print(f"\nLLM calls={calls} input_tokens={inp} output_tokens={out} elapsed={time.time()-t:.0f}s")
