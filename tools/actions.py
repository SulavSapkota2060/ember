from config import OUT_DIR
def write_action_draft(building_id: int, subject: str, body: str) -> str:
    p = OUT_DIR / f"action_{building_id}.md"
    p.write_text(f"# {subject}\n\n{body}\n")
    return str(p)
