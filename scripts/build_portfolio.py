"""Build out/portfolio_la.json from out/screen_la.json + live CAL FIRE zone lookups. Usage: python scripts/build_portfolio.py"""
import sys, json
sys.path.insert(0, ".")
from concurrent.futures import ThreadPoolExecutor
import portfolio
from config import OUT_DIR
from tools import hazard_zone

screen = json.load(open(OUT_DIR / "screen_la.json"))
def z(row):
    for _ in range(3):  # retry; a failed lookup stays "error" (never silently "not in zone")
        r = hazard_zone.get_hazard_zone(row["lat"], row["lon"], "wildfire")
        if r.get("status") == "ok":
            return str(row["id"]), r
    return str(row["id"]), r
with ThreadPoolExecutor(8) as ex:
    zones = dict(ex.map(z, [r for r in screen.values() if r.get("status") == 200]))
bad = [k for k, v in zones.items() if v.get("status") != "ok"]
rows = portfolio.build(screen, zones)
portfolio.FILE.write_text(json.dumps(rows))
print(f"{len(screen)} screened; {len(rows)} in portfolio; zone lookup failures: {len(bad)} {bad[:5]}")
print("in a mapped zone:", [(r['name'], r['zone']) for r in rows if r['zone'] not in ('none', 'unknown')])
print("missing carbon (no area):", sum(r['carbon_t'] is None for r in rows))
