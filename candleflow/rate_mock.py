from datetime import date
from typing import Literal

from fastapi import FastAPI, HTTPException

from candleflow.rates import RateQuote, exchange_rate

app = FastAPI(title="Мок валютных курсов")


@app.get("/rates/{currency}/{day}", response_model=RateQuote)
def quote(currency: Literal["RUB", "EUR", "USD"], day: date):
    if currency != "RUB" and day.weekday() >= 5:
        raise HTTPException(404, "Курс за эту дату не опубликован")
    return RateQuote(rate=exchange_rate(currency, day), day=day)
