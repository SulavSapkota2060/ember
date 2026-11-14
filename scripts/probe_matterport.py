"""Probe Matterport GraphQL with the .env credentials. Prints responses, never the secrets.
Usage: python scripts/probe_matterport.py [PROPERTY_ID]"""
import os, sys, json, base64, requests
sys.path.insert(0, ".")
import config  # loads .env
from tools import costar

URL = "https://api.matterport.com/api/models/graph"
k, t = os.environ.get("MATTERPORT_API_KEY"), os.environ.get("MATTERPORT_API_TOKEN")
pid = int(sys.argv[1]) if len(sys.argv) > 1 else 249226
models = costar.get_property(pid)["matterport_models"]
print("property", pid, "models", models)

def gql(q, auth):
    r = requests.post(URL, json={"query": q}, headers={"Authorization": auth}, timeout=30)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text[:300]

basic = "Basic " + base64.b64encode(f"{k}:{t}".encode()).decode()
q = '{ model(id:"%s") { id name visibility created } }' % models[0]
print("basic key:token ->", gql(q, basic))

if len(sys.argv) > 2 and sys.argv[2] == "schema":
    for tname in ("Model", "Floor", "Room", "PanoLocation", "Image", "Mattertag"):
        s, r = gql('{ __type(name:"%s") { fields { name type { name kind ofType { name kind } } } } }' % tname, basic)
        f = ((r.get("data") or {}).get("__type") or {}).get("fields") if isinstance(r, dict) else None
        print(tname, "->", [x["name"] for x in f] if f else (s, str(r)[:200]))
