import asyncio
import random
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException
from sqlalchemy.dialects.postgresql import insert

from candleflow.db import SessionLocal
from candleflow.models import MarkingReceipt

app = FastAPI(title="Мок сервиса маркировки")


def store_receipt(key: str, payload: dict) -> bool:
    with SessionLocal.begin() as db:
        statement = insert(MarkingReceipt).values(key=key, payload=payload)
        inserted = db.scalar(statement.on_conflict_do_nothing().returning(MarkingReceipt.key))
        if inserted:
            return True
        if db.get(MarkingReceipt, key).payload != payload:
            raise HTTPException(409, "Ключ уже использован для другого отчёта")
        return False


@app.post("/reports")
async def report(payload: dict, idempotency_key: Annotated[str, Header(min_length=1, max_length=100)]):
    with SessionLocal() as db:
        receipt = db.get(MarkingReceipt, idempotency_key)
        if receipt:
            if receipt.payload != payload:
                raise HTTPException(409, "Ключ уже использован для другого отчёта")
            return {"accepted": True, "duplicate": True}
    if random.randrange(10) < 3:
        raise HTTPException(500, "Смоделированный сбой")
    inserted = store_receipt(idempotency_key, payload)
    if random.randrange(10) == 0:
        await asyncio.sleep(10)
    return {"accepted": True, "duplicate": not inserted}
