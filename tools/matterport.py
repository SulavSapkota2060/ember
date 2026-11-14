"""Matterport (OPTIONAL layer). GraphQL endpoint per Matterport docs: https://api.matterport.com/api/models/graph
with Basic auth (token id : token secret). Field names NOT verified: check the schema reference / GraphiQL first.
TODO: get_model(model_id) -> name, sweeps, floors, mattertags; embed Showcase SDK viewer for the demo."""
def get_model(model_id: str) -> dict:
    return {"status": "not_implemented", "model_id": model_id}
