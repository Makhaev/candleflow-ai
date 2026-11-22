from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated

import httpx
from pydantic import BaseModel, Field, ValidationError

from candleflow.auth import DomainError
from candleflow.config import settings


class RateQuote(BaseModel):
    rate: Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=6)]
    day: date


def mock_quote(currency: str, day: date) -> RateQuote:
    if currency == "RUB":
        return RateQuote(rate=Decimal(1), day=day)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    base = {"USD": Decimal("91.25"), "EUR": Decimal("100.50")}
    return RateQuote(rate=base[currency] + Decimal(day.toordinal() % 31) / Decimal(100), day=day)


def exchange_rate(currency: str, day: date) -> Decimal:
    return mock_quote(currency, day).rate


async def fetch_quote(client: httpx.AsyncClient, currency: str, day: date) -> RateQuote:
    if currency == "RUB":
        return mock_quote(currency, day)
    try:
        for _ in range(8):
            response = await client.get(f"{settings.rates_url}/rates/{currency}/{day}")
            if response.status_code == 404 and day > date.min:
                day -= timedelta(days=1)
                continue
            response.raise_for_status()
            quote = RateQuote.model_validate_json(response.content)
            if quote.day != day:
                break
            return quote
    except (httpx.HTTPError, ValidationError) as exc:
        raise DomainError("Сервис курсов временно недоступен", 503) from exc
    raise DomainError("Не найден актуальный предыдущий курс", 503)
