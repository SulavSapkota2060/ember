# Ember
Hazard-readiness deep agent for commercial buildings (CoStar + Vexcel [+ Matterport optional]). HackPrinceton F26.
Risk scores are heuristic estimates, not a validated model.

## Run
    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env   # fill keys; never commit
    streamlit run app.py
Vexcel runs in MOCK mode (fixtures) until `VEXCEL_API_KEY` is set.
CoStar kit is read from `COSTAR_KIT_DIR` (default `../data/costar-hackathon-kit`, outside the repo; not redistributed).
