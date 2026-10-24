"""Audit-safe query traces: exact Crustdata JSON request + returned field names.

Never record API keys, raw people profiles or email addresses in history.
The actual provider payload is built by the same method used to send requests.
"""


def returned_field_paths(rows: list[dict], limit: int = 80) -> list[str]:
    keys = set()

    def walk(value, prefix="", depth=0):
        if depth > 7 or len(keys) >= 300:
            return
        if isinstance(value, dict):
            for name, child in value.items():
                path = f"{prefix}.{name}" if prefix else name
                if isinstance(child, (dict, list)):
                    walk(child, path, depth + 1)
                else:
                    keys.add(path)
        elif isinstance(value, list):
            for item in value[:3]:
                walk(item, prefix, depth + 1)

    for row in rows[:5]:
        walk(row)
    return sorted(keys)[:limit]


def search_trace(endpoint: str, request: dict, rows: list[dict],
                 cached: bool, *, demo: bool = False) -> dict:
    """Request is the actual provider query payload, never a guessed summary."""
    return {
        "endpoint": "DEMO" if demo else endpoint,
        "request": request,
        "result_count": len(rows),
        "returned_attributes": returned_field_paths(rows),
        "from_checkpoint": bool(cached),
        "provider_calls_this_run": 0 if cached or demo else 1,
        "note": "Returned fields reflect sampled records; missing fields are not inferred.",
    }
