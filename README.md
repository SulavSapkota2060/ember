# Ember: wildfire readiness for commercial buildings

HackPrinceton F26 (Sustainability track; CoStar, LangChain deepagents and Amazon practical-AI side tracks).

Wildfire risk is rising and most of the built environment is not retrofitted for it. Ember is an AI agent team that looks at a
commercial building **from outside (Vexcel aerial data) and inside (Matterport interiors)**, says **what is at stake (CoStar)**
and which fixes buy the most protection, and lets an owner test them live. Protecting an existing building avoids loss,
tenant displacement and rebuilding, and a rebuild re-emits the upfront embodied carbon.

> The risk score is a transparent **heuristic ESTIMATE, not a validated model**. Weights are our own starting guesses
> (see `scoring.py`). Nothing here predicts the probability of a fire.

## What it does
- **Outside:** Vexcel parcel attributes (tree cover by defensible-space zone, roof material/defects, unscreened vents),
  open-vocabulary detection on the aerial with numbered callouts, and the **CAL FIRE Fire Hazard Severity Zone** at the building.
- **Inside:** Matterport panoramas read by a vision model for extinguishers, alarms, exit signs, blocked exits and stored
  combustibles. A **second, skeptical vision pass must confirm every "seen" item** or it is downgraded to "cannot tell".
  Readings are uncertain, and "not seen" is never treated as absent.
- **At stake:** CoStar tenants, known employees, occupied area, last sale (with its price status).
- **Sustainability:** *embodied carbon at stake*, i.e. the upfront (A1-A3) carbon a rebuild would re-emit, from gross area x
  CLF benchmark averages for commercial buildings (structure 295, enclosure 108, interiors 21, MEP 128 kgCO2/m2; the total is our
  own sum). It is **not a forecast** (no probability of loss), a US average, and excludes transport and construction.
  Source and caveats are in `sustainability.py`.
- **Interact:** left rail with the live score and fix checkboxes (instant re-score, no LLM call), a budget planner that only
  uses costs *you* enter, one-click agent jobs, and a follow-up chat. The agent can tick the fixes on screen.

## Agent design (LangChain deepagents)
Orchestrator + three specialist subagents (`exterior-analyst`, `interior-inspector`, `stakes-analyst`) run in parallel; the
orchestrator then calls `score_risk` and writes the report. **The score is computed by code, never by the LLM.** Drafts (e.g. a
contractor email) pass a **human approval gate** (approve / edit / reject) before anything is saved.

## Data and limits (read this)
- CAL FIRE zone lookup uses the public statewide service, whose local-area map is from 2011 (older than CAL FIRE's newer
  recommendations); "outside the mapped zones" is not proof of low hazard. Only 1 of the 152 LA buildings we screened with
  Matterport models is inside a mapped zone.
- Vexcel's roof "condition" scale is undocumented, so it is not scored. Defensible-space zone distances are not documented,
  so none are stated.
- CoStar fields are often blank; blanks are shown as "not recorded", never estimated.
- Vision readings of interiors are uncertain even with the second look.
- The CoStar kit is read locally and is not redistributed in this repo.

## Run
    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env   # fill keys; the repo is public, .env is gitignored, never commit keys
    streamlit run app.py
Set `LLM_MODEL=anthropic:claude-sonnet-5-5` (provider:model). `LIGHT_MODEL` (stakes subagent) and `VISION_MODEL` are optional.
Vexcel runs in MOCK mode (fixtures) until `VEXCEL_API_KEY` is set. CoStar kit path: `COSTAR_KIT_DIR`
(default `../data/costar-hackathon-kit`).

Tests: `python -I tests/test_scoring.py`. Headless agent run: `python scripts/run_team.py BUILDING_ID`.
