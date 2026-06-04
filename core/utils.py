
from typing import Any, Optional
from datetime import date, datetime
from decimal import Decimal

def json_safe_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): json_safe_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe_value(v) for v in value]
    if isinstance(value, tuple):
        return [json_safe_value(v) for v in value]
    return value


def json_safe_dict(data: Optional[dict[str, Any]]) -> dict[str, Any]:
    return json_safe_value(data or {})