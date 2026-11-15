"""Pluggable hazard modules. Add an entry to support another hazard.
Scoring is a transparent HEURISTIC estimate, not a validated model."""

HAZARDS = {
    "wildfire": {
        "label": "Wildfire",
        "detect_labels": ["tree", "dry brush", "wood fence", "wood deck", "shingle roof", "gravel"],
        # Vexcel property attributes worth reading (names from the Vexcel tutorial)
        "attrs": ["parcel-tree-cover.percent", "defensible-space.zone1-tree",
                  "roof-characteristics.material.class", "roof-characteristics.condition"],
        "prompt": ("Assess WILDFIRE readiness: vegetation/fuel close to the structure (defensible space "
                   "zones), roof material and condition, combustible attachments (decks, fences)."),
    },
}
