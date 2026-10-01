def audit_event(action: str, entity_id: str) -> dict[str, str]:
    return {"action": action, "entity_id": entity_id}
