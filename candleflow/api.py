from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Cookie, Depends, FastAPI, Query, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from candleflow import inventory as inv
from candleflow.auth import DomainError, authenticate, login, token_hash
from candleflow.db import AsyncSessionLocal
from candleflow.models import Job, LoginSession, Material, Recipe
from candleflow.rates import fetch_quote
from candleflow.schemas import (
    Completion,
    Inspection,
    JobInput,
    Login,
    MaterialInput,
    Period,
    ReceiptInput,
    RecipeInput,
)

app = FastAPI(title="CandleFlow")
router = APIRouter(prefix="/api")
COOKIE = "candleflow_session"


def today() -> date:
    return datetime.now(ZoneInfo("Europe/Moscow")).date()


async def get_db():
    async with AsyncSessionLocal() as db:
        yield db


async def get_rate_client():
    async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
        yield client


DB = Annotated[AsyncSession, Depends(get_db)]
RateClient = Annotated[httpx.AsyncClient, Depends(get_rate_client)]


async def current_user(db: DB, token: Annotated[str | None, Cookie(alias=COOKIE)] = None):
    return await db.run_sync(authenticate, token)


User = Annotated[inv.User, Depends(current_user)]


@app.exception_handler(DomainError)
async def domain_error(_, exc: DomainError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def invalid_input(_, exc: RequestValidationError):
    fields = ", ".join(".".join(str(p) for p in e["loc"][1:]) for e in exc.errors())
    return JSONResponse({"detail": f"Проверьте формат полей: {fields}"}, status_code=422)


@app.exception_handler(IntegrityError)
async def integrity_error(_, exc):
    return JSONResponse({"detail": "Конфликт данных. Обновите страницу и повторите запрос."}, status_code=409)


@app.exception_handler(OperationalError)
async def operational_error(_, exc):
    return JSONResponse({"detail": "База данных временно недоступна"}, status_code=503)


def public_user(user: inv.User) -> dict:
    return {"id": user.id, "name": user.full_name, "role": user.role}


@router.post("/login")
async def sign_in(data: Login, response: Response, db: DB):
    user, token = await db.run_sync(login, data.login, data.password)
    await db.commit()
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=43200)
    return public_user(user)


@router.post("/logout")
async def sign_out(response: Response, db: DB, token: Annotated[str | None, Cookie(alias=COOKIE)] = None):
    entry = await db.get(LoginSession, token_hash(token)) if token else None
    if entry:
        await db.delete(entry)
        await db.commit()
    response.delete_cookie(COOKIE)
    return {"ok": True}


@router.get("/me")
def me(user: User):
    day = today()
    return {**public_user(user), "today": day, "week_start": day - timedelta(days=day.weekday())}


@app.get("/health")
def health():
    return {"ok": True, "at": datetime.now(UTC)}


@router.get("/materials")
async def materials(db: DB, user: User):
    inv.require(user, *inv.READERS)
    return [inv.serialize(row) for row in await db.scalars(select(Material).order_by(Material.code))]


@router.post("/materials", status_code=201)
async def create_material(data: MaterialInput, db: DB, user: User):
    result = await db.run_sync(inv.add_material, user, data)
    await db.commit()
    return result


@router.get("/recipes")
async def recipes(db: DB, user: User):
    inv.require(user, *inv.READERS)
    return [inv.serialize(row) for row in await db.scalars(select(Recipe).order_by(Recipe.id))]


@router.post("/recipes", status_code=201)
async def create_recipe(data: RecipeInput, db: DB, user: User):
    result = await db.run_sync(inv.add_recipe, user, data)
    await db.commit()
    return result


class BatchQuery(BaseModel):
    id: int | None = None
    material_code: str | None = None
    supplier: str | None = None
    received_on: date | None = None
    status: Literal["on_review", "accepted", "rejected"] | None = None
    currency: Literal["RUB", "USD", "EUR"] | None = None
    received: Decimal | None = None
    quantity: Decimal | None = None
    reserved: Decimal | None = None
    unit_price: Decimal | None = None
    unit_cost: Decimal | None = None
    exchange_rate: Decimal | None = None
    exchange_rate_on: date | None = None
    comment: str | None = None
    sort: Literal[
        "id",
        "material_code",
        "supplier",
        "received_on",
        "status",
        "currency",
        "received",
        "quantity",
        "reserved",
        "unit_price",
        "unit_cost",
        "exchange_rate",
        "exchange_rate_on",
        "comment",
    ] = "id"
    descending: bool = True
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


@router.get("/batches")
async def batches(query: Annotated[BatchQuery, Query()], db: DB, user: User):
    filters = query.model_dump(exclude={"sort", "descending", "offset", "limit"})
    return await db.run_sync(
        inv.batch_list,
        user,
        filters=filters,
        sort=query.sort,
        descending=query.descending,
        offset=query.offset,
        limit=query.limit,
    )


@router.post("/receipts", status_code=201)
async def receipt(data: ReceiptInput, db: DB, user: User, client: RateClient):
    inv.require(user, "storekeeper", "manager")
    quotes = {
        currency: await fetch_quote(client, currency, data.received_on)
        for currency in sorted({line.currency for line in data.lines})
    }
    result = await db.run_sync(inv.receive, user, data, quotes)
    await db.commit()
    return result


@router.get("/batches/{batch_id}")
async def batch(batch_id: int, db: DB, user: User):
    return await db.run_sync(inv.batch_history, user, batch_id)


@router.post("/batches/{batch_id}/inspection")
async def inspection(batch_id: int, data: Inspection, db: DB, user: User):
    result = await db.run_sync(inv.inspect_batch, user, batch_id, data.status)
    await db.commit()
    return result


@router.get("/stock/{material_code}")
async def stock(material_code: str, db: DB, user: User):
    return await db.run_sync(inv.stock, user, material_code)


@router.get("/jobs")
async def jobs(db: DB, user: User, offset: Annotated[int, Query(ge=0)] = 0):
    inv.require(user, *inv.READERS)
    keys = await db.scalars(select(Job.id).order_by(Job.id.desc()).offset(offset).limit(50))
    return await db.run_sync(lambda sync: [inv.job_detail(sync, user, key) for key in keys])


@router.get("/jobs/shortages")
async def shortages(period: Annotated[Period, Query()], db: DB, user: User):
    return await db.run_sync(inv.shortages, user, period)


@router.post("/jobs", status_code=201)
async def create_job(data: JobInput, db: DB, user: User):
    result = await db.run_sync(inv.plan, user, data)
    await db.commit()
    return result


@router.post("/jobs/drafts", status_code=201)
async def draft(data: JobInput, db: DB, user: User):
    result = await db.run_sync(inv.create_draft, user, data)
    await db.commit()
    return result


@router.post("/jobs/{job_id}/plan")
async def plan_existing(job_id: int, db: DB, user: User):
    result = await db.run_sync(inv.plan_draft, user, job_id)
    await db.commit()
    return result


@router.get("/jobs/{job_id}")
async def job(job_id: int, db: DB, user: User):
    return await db.run_sync(inv.job_detail, user, job_id)


@router.get("/jobs/{job_id}/cost")
async def cost(job_id: int, db: DB, user: User):
    return await db.run_sync(inv.job_cost, user, job_id)


@router.post("/jobs/{job_id}/{action}")
async def transition(job_id: int, action: Literal["start", "cancel"], db: DB, user: User):
    result = await db.run_sync(inv.job_transition, user, job_id, action)
    await db.commit()
    return result


@router.post("/completion/{job_id}")
async def completion(job_id: int, data: Completion, db: DB, user: User):
    result = await db.run_sync(inv.complete, user, job_id, data, today())
    await db.commit()
    return result


app.include_router(router)
