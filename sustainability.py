"""Sustainability layer: upfront embodied carbon "at stake" if a building were lost and had to be rebuilt.

SOURCE (read 2026-11-14): Carbon Leadership Forum Embodied Carbon Policy Reduction Calculator, documentation page
"Embodied Carbon Intensities", table "A1-A3 values per Building Typology and Physical Scope (all values in kgCO2/m2)",
Commercial row. Values are averages from the CLF WBLCA Benchmark Study v2 (199 commercial buildings; Benke et al. 2025,
Scientific Data 12:1085, doi 10.1038/s41597-025-05216-0). MEP is NOT from that study: the page uses a coarse estimate from
the MEP2040 Beginner's Guide.
https://ec-policy-reduction-calculator.carbonleadershipforum.org/documentation/embodied-carbon-intensities

What this is NOT: a forecast. No probability of loss is applied, so it is "carbon at stake", not "expected avoided
carbon". It is a US-wide commercial average (not LA-specific, not by building type), covers A1-A3 only (A4 transport
and A5 construction are excluded, so a rebuild would emit more), and the page states no total or uncertainty: the total
below is our own sum of the four listed scopes. Gross building area from CoStar is used as the floor-area basis.
"""
SQFT_TO_M2 = 0.092903
COMMERCIAL_A1_A3 = {"structure": 295, "enclosure": 108, "interiors": 21, "mep": 128}  # kgCO2/m2, per source above
SOURCE_URL = "https://ec-policy-reduction-calculator.carbonleadershipforum.org/documentation/embodied-carbon-intensities"
SOURCE_NAME = "CLF WBLCA Benchmark Study v2 averages (commercial, A1-A3), via CLF EC Policy Reduction Calculator"


def carbon_at_stake(gba_sqft) -> dict | None:
    """Tonnes CO2e of upfront (A1-A3) embodied carbon in the building's gross area. None if area unknown."""
    if not isinstance(gba_sqft, (int, float)) or gba_sqft <= 0:
        return None
    m2 = gba_sqft * SQFT_TO_M2
    per_scope = {k: round(m2 * v / 1000.0) for k, v in COMMERCIAL_A1_A3.items()}  # tonnes
    return {"gba_m2": round(m2), "intensity_kgco2_m2": sum(COMMERCIAL_A1_A3.values()),
            "tonnes_co2e": sum(per_scope.values()), "by_scope_tonnes": per_scope,
            "basis": SOURCE_NAME, "source_url": SOURCE_URL,
            "caveats": "US commercial average, A1-A3 only (rebuild would emit more); not a forecast: no probability "
                       "of loss applied; total is our sum of four listed scopes; CoStar gross area used as floor area."}
