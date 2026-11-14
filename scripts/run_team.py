"""Run the orchestrator + subagents end to end and print lanes/events. Usage: python scripts/run_team.py BUILDING_ID"""
import sys, time
sys.path.insert(0, ".")
import agent as ag
bid = int(sys.argv[1])
a = ag.build_agent("wildfire", memory=True)
t = time.time(); use = {}
for ev in ag.stream_events(a, bid, "wildfire", thread_id="t"):
    if ev["type"] == "tool_call":
        print(f"[{time.time()-t:5.1f}s] {ev['lane']:>20} CALL {ev['name']} {str(ev['args'])[:90]}")
    elif ev["type"] == "tool_result":
        print(f"[{time.time()-t:5.1f}s] {ev['lane']:>20} RESULT {ev['name']} {ev['content'][:80]!r}")
    elif ev["type"] == "usage":
        u = use.setdefault(ev["lane"], [0, 0, 0]); u[0] += ev["input"]; u[1] += ev["output"]; u[2] += 1
    elif ev["type"] == "text":
        print("\nFINAL:\n" + ev["text"])
print(f"elapsed={time.time()-t:.0f}s")
print("tokens by lane (in, out, llm calls):", use, "| total in/out:", sum(v[0] for v in use.values()), sum(v[1] for v in use.values()))
