import re
from decimal import Decimal, InvalidOperation
from typing import Any

ALIAS = re.compile(r"\b(?:SUPPLIER|EMPLOYEE|CATEGORY|PERSON_REDACTED)_\d+\b", re.IGNORECASE)
NUMBER = re.compile(r"[−+-]?\d+(?:[.,]\d+)?(?:[eE][+-]?\d+)?")
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
RU_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
# Rounded figures are accepted only when they keep at least one fractional digit
# or are large enough that rounding to an integer cannot invent a small count.
ROUNDED_INTEGER_FLOOR = Decimal(100)


def numbers(value: Any) -> set[Decimal]:
    if isinstance(value, dict):
        return set().union(*(numbers(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(numbers(item) for item in value))
    if isinstance(value, bool) or value is None:
        return set()
    text = ALIAS.sub("", str(value))
    text = ISO_DATE.sub(r"\1 \2 \3", text)
    text = RU_DATE.sub(r"\1 \2 \3", text)
    result = set()
    for token in NUMBER.findall(text):
        try:
            result.add(Decimal(token.replace("−", "-").replace(",", ".")))
        except InvalidOperation:
            result.add(Decimal("Infinity"))
    return result


def supported(found: set[Decimal], evidence: set[Decimal]) -> bool:
    """Every number must equal a tool/interpreter value or be its rounding to the shown precision."""
    finite = [item for item in evidence if item.is_finite()]
    for value in found - evidence:
        if not value.is_finite():
            return False
        digits = max(0, -value.as_tuple().exponent)
        if not digits and abs(value) < ROUNDED_INTEGER_FLOOR:
            return False
        tolerance = Decimal(5).scaleb(-digits - 1)
        if not any(abs(item - value) <= tolerance for item in finite):
            return False
    return True


def interpreter_numbers(output: list[dict]) -> set[Decimal]:
    return set().union(
        *(
            numbers(log["logs"])
            for call in output
            if call["type"] == "code_interpreter_call"
            for log in (call.get("outputs") or [])
            if log.get("type") == "logs" and log.get("logs")
        )
    )
