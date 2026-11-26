import logging
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

import httpx
from anyio import to_thread
from fastapi import APIRouter, Cookie, Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyCookie
from pydantic import BaseModel, Field, StringConstraints, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from candleflow import api_views as view
from candleflow import auth
from candleflow import inventory as inv
from candleflow.auth import DomainError, authenticate, token_hash
from candleflow.config import settings
from candleflow.db import AsyncSessionLocal
from candleflow.models import LoginSession, Material, Recipe
from candleflow.observability import RequestContextMiddleware, configure_logging, request_id
from candleflow.rate_limit import LOGIN, RateLimited, limiter
from candleflow.rates import fetch_quote
from candleflow.schemas import (
    Code,
    Completion,
    Inspection,
    JobInput,
    Login,
    MaterialInput,
    Period,
    ReceiptInput,
    RecipeInput,
)

configure_logging(settings.log_level)
logger = logging.getLogger(__name__)
app = FastAPI(title="CandleFlow")
app.add_middleware(RequestContextMiddleware)
router = APIRouter(prefix="/api", tags=["Учёт"], responses=view.errors(422, 503))
COOKIE = "candleflow_session"
WORKSHOP_TIME = ZoneInfo("Europe/Moscow")
session_cookie = APIKeyCookie(name=COOKIE, scheme_name="CandleFlowSession", auto_error=False)
PageOffset = Annotated[int, Query(ge=0)]
PageLimit = Annotated[int, Query(ge=1, le=100)]


def today() -> date:
    return datetime.now(WORKSHOP_TIME).date()


async def get_db():
    async with AsyncSessionLocal() as db:
        yield db


async def get_rate_client():
    async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
        yield client


DB = Annotated[AsyncSession, Depends(get_db)]
RateClient = Annotated[httpx.AsyncClient, Depends(get_rate_client)]


async def current_user(db: DB, token: Annotated[str | None, Depends(session_cookie)]):
    return await db.run_sync(authenticate, token)


User = Annotated[inv.User, Depends(current_user)]


@app.exception_handler(DomainError)
async def domain_error(_, exc: DomainError):
    headers = {"Retry-After": str(exc.retry_after)} if isinstance(exc, RateLimited) else None
    return JSONResponse({"detail": str(exc)}, status_code=exc.status, headers=headers)


@app.exception_handler(RequestValidationError)
async def invalid_input(_, exc: RequestValidationError):
    fields = ", ".join(".".join(str(p) for p in e["loc"][1:]) for e in exc.errors())
    return JSONResponse({"detail": f"Проверьте формат полей: {fields}"}, status_code=422)


@app.exception_handler(IntegrityError)
async def integrity_error(_, exc):
    return JSONResponse({"detail": "Конфликт данных. Обновите страницу и повторите запрос."}, status_code=409)


@app.exception_handler(OperationalError)
async def operational_error(_, exc):
    logger.error("database unavailable: %s", type(exc).__name__)
    return JSONResponse({"detail": "База данных временно недоступна"}, status_code=503)


@app.exception_handler(Exception)
async def unexpected_error(_, exc: Exception):
    # The traceback goes to the log; the user gets a readable message and an ID to report.
    logger.exception("unhandled error")
    return JSONResponse(
        {
            "detail": f"Не удалось выполнить запрос. Повторите попытку, а если ошибка останется, "
            f"сообщите код {request_id.get()[:8]}."
        },
        status_code=500,
    )


def public_user(user: inv.User) -> dict:
    return {"id": user.id, "name": user.full_name, "role": user.role}


@router.post(
    "/login", response_model=view.SessionView, tags=["Сессия"], responses=view.errors(401), summary="Войти"
)
async def sign_in(
    data: Login,
    request: Request,
    response: Response,
    db: DB,
    current_token: Annotated[str | None, Cookie(alias=COOKIE)] = None,
):
    client = request.client.host if request.client else "unknown"
    await limiter.check(LOGIN, f"{data.login.casefold()}:{client}")
    user = await db.run_sync(auth.find_user, data.login)
    if not await to_thread.run_sync(auth.verify_password, user, data.password):
        raise DomainError("Неверный логин или пароль", 401)
    token = await db.run_sync(auth.open_session, user)
    if current_token:
        previous = await db.get(LoginSession, token_hash(current_token))
        if previous:
            await db.delete(previous)
    await db.commit()
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=43200)
    return me(user)


@router.post("/logout", response_model=view.OkView, tags=["Сессия"], summary="Выйти")
async def sign_out(response: Response, db: DB, token: Annotated[str | None, Cookie(alias=COOKIE)] = None):
    entry = await db.get(LoginSession, token_hash(token)) if token else None
    if entry:
        await db.delete(entry)
        await db.commit()
    response.delete_cookie(COOKIE)
    return {"ok": True}


@router.get(
    "/me",
    response_model=view.SessionView,
    tags=["Сессия"],
    responses=view.errors(401),
    summary="Текущая сессия",
)
def me(user: User):
    day = today()
    return {**public_user(user), "today": day, "week_start": day - timedelta(days=day.weekday())}


@app.get("/health", response_model=view.HealthView, summary="Проверка доступности API")
def health():
    return {"ok": True, "at": datetime.now(UTC)}


@router.get(
    "/materials",
    response_model=list[view.MaterialView],
    responses=view.errors(401, 403),
    summary="Список материалов",
)
async def materials(db: DB, user: User):
    inv.require(user, *inv.READERS)
    return [inv.serialize(row) for row in await db.scalars(select(Material).order_by(Material.code))]


@router.post(
    "/materials",
    status_code=201,
    response_model=view.MaterialView,
    responses=view.errors(401, 403, 409),
    summary="Создать материал",
)
async def create_material(data: MaterialInput, db: DB, user: User):
    result = await db.run_sync(inv.add_material, user, data)
    await db.commit()
    return result


@router.get(
    "/recipes",
    response_model=list[view.RecipeView],
    responses=view.errors(401, 403),
    summary="Список рецептов",
)
async def recipes(db: DB, user: User):
    inv.require(user, *inv.READERS)
    return [inv.serialize(row) for row in await db.scalars(select(Recipe).order_by(Recipe.id))]


@router.post(
    "/recipes",
    status_code=201,
    response_model=view.RecipeView,
    responses=view.errors(401, 403, 404, 409),
    summary="Создать рецепт",
)
async def create_recipe(data: RecipeInput, db: DB, user: User):
    result = await db.run_sync(inv.add_recipe, user, data)
    await db.commit()
    return result


class BatchQuery(BaseModel):
    id: int | None = None
    receipt_id: int | None = None
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
        "receipt_id",
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


@router.get(
    "/batches",
    response_model=view.BatchPage,
    responses=view.errors(401, 403),
    summary="Фильтрация и пагинация партий",
)
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


@router.post(
    "/receipts",
    status_code=201,
    response_model=list[view.BatchView],
    responses=view.errors(401, 403, 404, 409),
    summary="Создать поступление и его партии",
)
async def receipt(data: ReceiptInput, db: DB, user: User, client: RateClient):
    inv.require(user, "storekeeper", "manager")
    quotes = {
        currency: await fetch_quote(client, currency, data.received_on)
        for currency in sorted({line.currency for line in data.lines})
    }
    result = await db.run_sync(inv.receive, user, data, quotes)
    await db.commit()
    return result


@router.get(
    "/batches/{batch_id}",
    response_model=view.BatchDetails,
    responses=view.errors(401, 403, 404),
    summary="Карточка партии с аудитом",
)
async def batch(batch_id: int, db: DB, user: User):
    return await db.run_sync(inv.batch_history, user, batch_id)


@router.post(
    "/batches/{batch_id}/inspection",
    response_model=view.BatchView,
    responses=view.errors(401, 403, 404, 409),
    summary="Принять или забраковать партию",
)
async def inspection(batch_id: int, data: Inspection, db: DB, user: User):
    result = await db.run_sync(inv.inspect_batch, user, batch_id, data.status)
    await db.commit()
    return result


@router.get(
    "/stock/{material_code}",
    response_model=view.StockView,
    responses=view.errors(401, 403, 404),
    summary="Остатки материала",
)
async def stock(material_code: Code, db: DB, user: User):
    return await db.run_sync(inv.stock, user, material_code)


@router.get(
    "/jobs",
    response_model=view.JobPage,
    responses=view.errors(401, 403),
    summary="Список производственных заданий",
)
async def jobs(
    db: DB,
    user: User,
    status: view.JobStatus | None = None,
    offset: PageOffset = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return await db.run_sync(inv.job_list, user, offset, limit, status)


@router.get(
    "/jobs/shortages",
    response_model=list[view.JobView],
    responses=view.errors(401, 403),
    summary="Дефициты за период",
)
async def shortages(period: Annotated[Period, Query()], db: DB, user: User):
    return await db.run_sync(inv.shortages, user, period)


@router.post(
    "/jobs",
    status_code=201,
    response_model=view.JobView,
    responses=view.errors(401, 403, 404, 409),
    summary="Создать и запланировать задание одной операцией",
)
async def create_job(data: JobInput, db: DB, user: User):
    result = await db.run_sync(inv.plan, user, data)
    await db.commit()
    return result


@router.post(
    "/jobs/drafts",
    status_code=201,
    response_model=view.JobView,
    responses=view.errors(401, 403, 404, 409),
    summary="Создать черновик задания",
)
async def draft(data: JobInput, db: DB, user: User):
    result = await db.run_sync(inv.create_draft, user, data)
    await db.commit()
    return result


@router.post(
    "/jobs/{job_id}/plan",
    response_model=view.JobView,
    responses=view.errors(401, 403, 404, 409),
    summary="Запланировать черновик и зарезервировать материал",
)
async def plan_existing(job_id: int, db: DB, user: User):
    result = await db.run_sync(inv.plan_draft, user, job_id)
    await db.commit()
    return result


@router.get(
    "/jobs/{job_id}",
    response_model=view.JobView,
    responses=view.errors(401, 403, 404),
    summary="Карточка задания",
)
async def job(job_id: int, db: DB, user: User):
    return await db.run_sync(inv.job_detail, user, job_id)


@router.get(
    "/jobs/{job_id}/cost",
    response_model=view.CostView,
    responses=view.errors(401, 403, 404),
    summary="Себестоимость задания",
)
async def cost(job_id: int, db: DB, user: User):
    return await db.run_sync(inv.job_cost, user, job_id)


@router.post(
    "/jobs/{job_id}/start",
    response_model=view.JobView,
    responses=view.errors(401, 403, 404, 409),
    summary="Запустить задание и списать сырьё",
)
async def start_job(job_id: int, db: DB, user: User):
    result = await db.run_sync(inv.job_transition, user, job_id, "start")
    await db.commit()
    return result


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=view.JobView,
    responses=view.errors(401, 403, 404, 409),
    summary="Отменить задание и снять резерв",
)
async def cancel_job(job_id: int, db: DB, user: User):
    result = await db.run_sync(inv.job_transition, user, job_id, "cancel")
    await db.commit()
    return result


@router.post("/completion/{job_id}", response_model=view.JobView, include_in_schema=False)
@router.post(
    "/jobs/{job_id}/complete",
    response_model=view.JobView,
    responses=view.errors(401, 403, 404, 409),
    summary="Завершить задание по фактическому расходу",
)
async def completion(job_id: int, data: Completion, db: DB, user: User):
    result = await db.run_sync(inv.complete, user, job_id, data, today())
    await db.commit()
    return result


@router.get(
    "/materials/{code}",
    response_model=view.MaterialView,
    responses=view.errors(401, 403, 404),
    summary="Карточка материала",
)
async def material(code: Code, db: DB, user: User):
    return await db.run_sync(inv.material_view, user, code)


@router.get(
    "/recipes/{recipe_id}",
    response_model=view.RecipeDetails,
    responses=view.errors(401, 403, 404),
    summary="Рецепт и нормы расхода",
)
async def recipe(recipe_id: int, db: DB, user: User):
    return await db.run_sync(inv.recipe_view, user, recipe_id)


@router.get(
    "/receipts",
    response_model=list[view.ReceiptView],
    responses=view.errors(401, 403),
    summary="Список поступлений",
)
async def receipts(db: DB, user: User, offset: PageOffset = 0, limit: PageLimit = 20):
    return await db.run_sync(inv.receipt_list, user, offset, limit)


@router.get(
    "/receipts/{receipt_id}",
    response_model=view.ReceiptDetails,
    responses=view.errors(401, 403, 404),
    summary="Поступление и его партии",
)
async def receipt_details(receipt_id: int, db: DB, user: User):
    return await db.run_sync(inv.receipt_view, user, receipt_id)


@router.get(
    "/batches/{batch_id}/history",
    response_model=view.HistoryView,
    responses=view.errors(401, 403, 404),
    summary="Аудит и движения по партии",
)
async def batch_history(batch_id: int, db: DB, user: User, offset: PageOffset = 0, limit: PageLimit = 20):
    return await db.run_sync(inv.history, user, "batches", batch_id, offset, limit)


@router.get(
    "/jobs/{job_id}/history",
    response_model=view.HistoryView,
    responses=view.errors(401, 403, 404),
    summary="Аудит и движения по заданию",
)
async def job_history(job_id: int, db: DB, user: User, offset: PageOffset = 0, limit: PageLimit = 20):
    return await db.run_sync(inv.history, user, "production_jobs", job_id, offset, limit)


class AuditQuery(BaseModel):
    entity: Literal["materials", "recipes", "receipts", "batches", "production_jobs"] | None = None
    actor: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)] | None = (
        None
    )
    date_from: date | None = None
    date_to: date | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def valid_dates(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("Начало периода должно быть не позже конца")
        return self


@router.get(
    "/audit",
    response_model=view.AuditPage,
    responses=view.errors(401, 403),
    summary="Журнал аудита руководителя",
)
async def audit_log(query: Annotated[AuditQuery, Query()], db: DB, user: User):
    start = datetime.combine(query.date_from, time.min, WORKSHOP_TIME) if query.date_from else None
    end = datetime.combine(query.date_to, time.max, WORKSHOP_TIME) if query.date_to else None
    return await db.run_sync(
        inv.list_audit,
        user,
        entity=query.entity,
        actor=query.actor,
        date_from=start,
        date_to=end,
        offset=query.offset,
        limit=query.limit,
    )


app.include_router(router)
