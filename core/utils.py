
import secrets
import string
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


def _random_public_code(length: int = 6) -> str:
    return "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(length))


def _generate_prefixed_public_id(prefix: str, model_cls, field_name: str = "public_id") -> str:
    while True:
        candidate = f"{prefix}-{_random_public_code()}"
        if not model_cls._base_manager.filter(**{field_name: candidate}).exists():
            return candidate