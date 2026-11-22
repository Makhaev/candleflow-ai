from datetime import date, timedelta
from decimal import Decimal


def exchange_rate(currency: str, day: date) -> Decimal:
    """Deterministic simulated daily quotes, published Monday to Friday."""
    if currency == "RUB":
        return Decimal(1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    base = {"USD": Decimal("91.25"), "EUR": Decimal("100.50")}
    return base[currency] + Decimal(day.toordinal() % 31) / Decimal(100)
