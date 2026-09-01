"""Safe serialization helpers shared by HTTP presentation modules."""

from __future__ import annotations

import json
from typing import Any


def load_json_list(value: str | None) -> list[Any]:
    """Decode a JSON list, returning an empty list for invalid data.

    Args:
        value: JSON text read from persistence.

    Returns:
        The decoded list, or an empty list when input is absent or malformed.
    """
    if not value:
        return []
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return []
    return decoded if isinstance(decoded, list) else []
