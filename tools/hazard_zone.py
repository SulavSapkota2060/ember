"""Public hazard layer lookup. NOT VERIFIED REACHABLE. Degrades to 'unavailable'.
TODO: wire FEMA NFHL (flood) or a CAL FIRE FHSZ layer (wildfire) once the hazard is chosen and the URL is verified."""
def get_hazard_zone(lat: float, lon: float, hazard: str) -> dict:
    return {"zone": None, "status": "unavailable",
            "note": "hazard-zone lookup not implemented/verified; score from imagery and parcel attributes"}
