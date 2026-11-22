import re
from decimal import Decimal, InvalidOperation
from typing import Any

ALIAS = re.compile(r"\b(?:SUPPLIER|EMPLOYEE|CATEGORY|PERSON_REDACTED)_\d+\b", re.IGNORECASE)
NUMBER = re.compile(r"[−+-]?\d+(?:[.,]\d+)?(?:[eE][+-]?\d+)?")
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def numbers(value: Any) -> set[Decimal]:
    if isinstance(value, dict):
        return set().union(*(numbers(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(numbers(item) for item in value))
    if isinstance(value, bool) or value is None:
        return set()
    text = ALIAS.sub("", str(value))
    text = ISO_DATE.sub(r"\1 \2 \3", text)
    result = set()
    for token in NUMBER.findall(text):
        try:
            result.add(Decimal(token.replace("−", "-").replace(",", ".")))
        except InvalidOperation:
            result.add(Decimal("Infinity"))
    return result


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
