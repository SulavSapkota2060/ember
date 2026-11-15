# Ember: wildfire readiness for commercial buildings

HackPrinceton F26 (Sustainability track; CoStar, LangChain deepagents and Amazon practical-AI side tracks).

Wildfires can reach commercial buildings, and a loss displaces tenants and forces a rebuild that re-emits the building's
embodied carbon. Ember is an AI agent team that looks at a commercial building **from outside (Vexcel aerial data) and inside (Matterport interiors)**, says **what is at stake (CoStar)**
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
- **Equity lens:** the neighborhood's pollution-and-vulnerability burden (OEHHA CalEnviroScreen 4.0, census-tract percentile,
  looked up by coordinates). It is shown beside the exposure score and in the portfolio view as context. It is **not** a fire
  metric, describes the tract and not the building's tenants, and is **not part of the score**.
- **Portfolio:** 138 LA buildings with Matterport models, plotted by a **screening** exposure score (outside data only: tree
  cover by zone, roof material, CoStar sprinklers/construction, CAL FIRE zone) against embodied carbon at stake. Dot size is known
  employees (CoStar), colour is neighborhood burden or CAL FIRE zone, and the dashed lines are portfolio medians, not danger
  thresholds. The screening score is rougher than a full investigation (it has no detections, vents or interior; it ran 1-9 points
  lower on the 4 buildings we compared) and is labelled as such. Building data comes from `scripts/screen_la.py` and
  `scripts/build_portfolio.py` (output goes to the ignored `out/` folder).
- **Interact:** left rail with the live score and fix checkboxes (instant re-score, no LLM call), a budget planner that only
  uses costs *you* enter, one-click agent jobs, and a follow-up chat. The agent can tick the fixes on screen.

## Agent design (LangChain deepagents)
**Fast mode (default, about 5-11 s):** a deep agent plans (todo tool), then calls one `assess` tool that runs the specialist
workers **in parallel** (exterior: Vexcel + CAL FIRE; interior: Matterport images read by a cheap model, then every "seen"
claim must be confirmed by a stronger model; stakes: CoStar + carbon) and scores the building **in code**. The model then writes one short,
streamed report. A **code reviewer** checks that every number in the report appears in the evidence. Data for the selected
building is preloaded while you read the stakes. The workers are code (plus the vision models), not separate LLM agents.

**Deep mode (about 35-50 s):** an orchestrator delegates to three **LLM subagents** (`exterior-analyst`, `interior-inspector`,
`stakes-analyst`) and sends its draft to an independent **LLM reviewer** that audits it against the evidence; the orchestrator
revises. The Agent team tab shows the real messages agents sent each other, plus an activity timeline (tool calls, results, messages and
the API's reasoning summaries) and tokens per agent.

In both modes **the score is computed by code, never by the LLM.** Drafts (e.g. a contractor email) pass a **human approval gate**
(approve / edit / reject) before anything is saved.

## Data and limits (read this)
- CAL FIRE zone lookup uses the public statewide service, whose local-area map is from 2011 (older than CAL FIRE's newer
  recommendations); "outside the mapped zones" is not proof of low hazard. Only 1 of the 138 LA buildings in the portfolio view
  (152 screened; 138 had usable Vexcel data) is inside a mapped zone.
- Vexcel's roof "condition" scale is undocumented, so it is not scored. Defensible-space zone distances are not documented,
  so none are stated.
- CoStar fields are often blank; blanks are shown as "not recorded", never estimated.
- Vision readings of interiors are uncertain even with the second look.
- The CoStar kit is read locally and is not redistributed in this repo.

## What we built vs. what we used
**Written by us during the event (Nov 13-15, 2026):** all application code in this repo. Notably the exposure scoring heuristic and fix
catalog (`scoring.py`), the agent design in both modes, the parallel workers, the number-checking reviewer and its tests (`agent.py`,
`review.py`), the Haiku-then-Sonnet interior reading with a second look (`tools/interior.py`), the carbon-at-stake calculation
(`sustainability.py`), the equity lens (`equity.py`), the portfolio view (`portfolio.py`) and the Streamlit interface (`app.py`).
**Used, not written by us:** the libraries, models and datasets credited below.

**AI assistance:** this project was built with the help of Claude Code (Anthropic's AI coding assistant), working from the author's
direction. The problem, data sources, scoring factors and agent design were the author's choices, and the author takes responsibility
for the design decisions described here. The product also uses Claude models at run time (the agents and the interior image reading),
as credited below.

## Sources and credits
**Data**
- **CoStar:** hackathon data kit (commercial property, tenancy/location-occupancy, company detail and sale-comp records). Not redistributed.
- **Vexcel Imaging:** Vexcel Data Program API 2.0 (parcel/structure attributes, orthoimagery, open-vocabulary element detection).
  Imagery and derived attributes (c) Vexcel Imaging US, Inc.
- **Matterport:** GraphQL API (model thumbnails and panorama images of CoStar-linked scans). Scans belong to their owners and
  Matterport; "Open 3D walkthrough" links open Matterport's own viewer.
- **CAL FIRE:** [Fire Hazard Severity Zones](https://services.gis.ca.gov/arcgis/rest/services/Environment/Fire_Severity_Zones/MapServer)
  (State Responsibility Areas, 2007 map; Local Responsibility Areas, 2011 map). The service states that the State of California and
  CAL FIRE make no representations or warranties about the accuracy of the data or maps.
- **OEHHA (California EPA):** [CalEnviroScreen 4.0](https://oehha.ca.gov/calenviroscreen/download-data) results, via OEHHA's public
  ArcGIS feature service, used for the neighborhood-burden percentile.
- **Carbon Leadership Forum (CLF):** commercial A1-A3 embodied-carbon intensities from the
  [EC Policy Reduction Calculator documentation](https://ec-policy-reduction-calculator.carbonleadershipforum.org/documentation/embodied-carbon-intensities),
  which are averages from the CLF WBLCA Benchmark Study v2 (199 commercial buildings): Benke, B., Chafart, M., Shen, Y., Ashtiani, M.,
  Carlisle, S. & Simonen, K. (2025), "A Harmonized Dataset of High-Resolution Embodied Life Cycle Assessment Results for Buildings in
  North America", *Scientific Data* 12(1), 1085, [doi:10.1038/s41597-025-05216-0](https://doi.org/10.1038/s41597-025-05216-0).
  The MEP value on that page is a coarse estimate from the MEP2040 Beginner's Guide (Houde, Jensen & Shinn, 2025). Our total is the sum of
  the four listed scopes.

**Software and models**
- [LangChain deepagents](https://github.com/langchain-ai/deepagents) 0.7.38 (agents, subagents, planning, human-in-the-loop
  approvals), [LangChain](https://github.com/langchain-ai/langchain) 1.5.0 (`langchain-anthropic` 1.7.10, agent middleware) and
  [LangGraph](https://github.com/langchain-ai/langgraph) 1.2.22 (checkpointer and interrupts). Versions are the ones we ran.
- [Anthropic](https://www.anthropic.com) Claude models (Sonnet 5.5 and Haiku 5.5) for the agents and the interior image reading.
- [Streamlit](https://streamlit.io) 1.67 (interface and Graphviz rendering), [Altair](https://altair-viz.github.io) /
  [Vega-Lite](https://vega.github.io/vega-lite/) (charts), [pandas](https://pandas.pydata.org), [Pillow](https://python-pillow.org),
  [Requests](https://requests.readthedocs.io), [python-dotenv](https://github.com/theskumar/python-dotenv) and
  [Shapely](https://shapely.readthedocs.io). Each is used under its own license; none is modified or redistributed here.

Names of companies and agencies are used only to attribute data. This project is not affiliated with or endorsed by CoStar, Vexcel,
Matterport, CAL FIRE, OEHHA, the Carbon Leadership Forum, Anthropic or LangChain.

## Run
    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env   # fill keys; the repo is public, .env is gitignored, never commit keys
    streamlit run app.py
Set `LLM_MODEL=anthropic:claude-sonnet-5-5` (provider:model). Optional: `LIGHT_MODEL` (stakes subagent and interior vision first pass),
`VISION_MODEL` (interior second look), `DEMO_BUILDING_IDS` (buildings preloaded at start).
Vexcel runs in MOCK mode (fixtures) until `VEXCEL_API_KEY` is set. CoStar kit path: `COSTAR_KIT_DIR`
(default `../data/costar-hackathon-kit`).

Tests (plain asserts, no network): `python -I tests/test_scoring.py`, `tests/test_review.py` and `tests/test_equity.py`. Headless agent run: `python scripts/run_team.py BUILDING_ID`.
