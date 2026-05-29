
from typing import Any, Iterable


def find_first_key_recursive(
    data: Any,
    keys: Iterable[str] = ("status", "event", "type"),
) -> str:
    """
    Recursively search nested dicts/lists and return the first matching
    non-empty string value for one of the given keys.
    """
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        for value in data.values():
            found = find_first_key_recursive(value, keys)
            if found:
                return found

    elif isinstance(data, list):
        for item in data:
            found = find_first_key_recursive(item, keys)
            if found:
                return found

    return ""