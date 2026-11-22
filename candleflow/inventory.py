from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi.encoders import jsonable_encoder
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from candleflow.auth import DomainError, require
from candleflow.models import (
    Allocation,
    Audit,
    Batch,
    Component,
    Ingredient,
    Job,
    Material,
    Outbox,
    Recipe,
    User,
)
from candleflow.rates import exchange_rate
from candleflow.schemas import Completion, JobInput, MaterialInput, Period, ReceiptInput, RecipeInput

ZERO = Decimal(0)
READERS = ("warehouse", "technologist", "manager")
PRODUCTION = ("technologist", "manager")


def serialize(value: object) -> dict:
    return jsonable_encoder(vars(value), custom_encoder={Decimal: str})


def audit(db: Session, user: User, obj: object, before: dict | None = None) -> None:
    db.flush()
    key = obj.code if isinstance(obj, Material) else obj.id
    db.add(
        Audit(
            actor=user.full_name,
            entity=obj.__tablename__,
            entity_id=str(key),
            before=before,
            after=serialize(obj),
        )
    )


def get_or_fail(db: Session, model, key, *, lock: bool = False):
    obj = db.get(model, key, with_for_update=lock)
    if obj is None:
        raise DomainError("Запись не найдена", 404)
    return obj


def add_material(db: Session, user: User, data: MaterialInput) -> dict:
    require(user, "warehouse", "manager")
    if db.get(Material, data.code):
        raise DomainError("Материал с таким кодом уже существует")
    material = Material(**data.model_dump())
    db.add(material)
    audit(db, user, material)
    return serialize(material)


def add_recipe(db: Session, user: User, data: RecipeInput) -> dict:
    require(user, *PRODUCTION)
    if db.scalar(select(Recipe.id).where(Recipe.name == data.name)):
        raise DomainError("Рецепт с таким названием уже существует")
    recipe = Recipe(name=data.name)
    db.add(recipe)
    db.flush()
    for code, quantity in data.ingredients.items():
        get_or_fail(db, Material, code)
        db.add(Ingredient(recipe_id=recipe.id, material_code=code, per_unit=quantity))
    audit(db, user, recipe)
    return serialize(recipe)


def receive(db: Session, user: User, receipt: ReceiptInput) -> list[dict]:
    require(user, "warehouse", "manager")
    batches = []
    for line in receipt.lines:
        get_or_fail(db, Material, line.material_code)
        rate = exchange_rate(line.currency, receipt.received_on)
        batch = Batch(
            material_code=line.material_code,
            supplier=receipt.supplier,
            received_on=receipt.received_on,
            received=line.quantity,
            quantity=line.quantity,
            unit_price=line.unit_price,
            currency=line.currency,
            exchange_rate=rate,
            unit_cost=line.unit_price * rate,
            status="inspection",
            comment=line.comment,
        )
        db.add(batch)
        audit(db, user, batch)
        batches.append(serialize(batch))
    return batches


def inspect_batch(db: Session, user: User, batch_id: int, status: str) -> dict:
    require(user, "warehouse", "manager")
    batch = get_or_fail(db, Batch, batch_id, lock=True)
    if batch.status != "inspection" or status not in ("accepted", "rejected"):
        raise DomainError("Решение по проверке партии уже принято или статус недопустим")
    before = serialize(batch)
    batch.status = status
    audit(db, user, batch, before)
    return serialize(batch)


def batch_history(db: Session, user: User, batch_id: int) -> dict:
    require(user, *READERS)
    batch = get_or_fail(db, Batch, batch_id)
    rows = db.scalars(
        select(Audit).where(Audit.entity == "batches", Audit.entity_id == str(batch_id)).order_by(Audit.id)
    )
    return {"batch": serialize(batch), "history": [serialize(row) for row in rows]}


def batch_list(
    db: Session, user: User, *, filters: dict, sort: str, descending: bool, offset: int, limit: int
) -> dict:
    require(user, *READERS)
    columns = {column.name: getattr(Batch, column.name) for column in Batch.__table__.columns}
    query = select(Batch)
    for key, value in filters.items():
        if value is None or value == "":
            continue
        column = columns[key]
        query = query.where(
            column.icontains(value, autoescape=True) if key in {"supplier", "comment"} else column == value
        )
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    column = columns[sort]
    rows = db.scalars(
        query.order_by(column.desc() if descending else column.asc(), Batch.id).offset(offset).limit(limit)
    )
    return {"total": total, "items": [serialize(row) for row in rows]}


def stock(db: Session, user: User, material_code: str) -> dict:
    require(user, *READERS)
    material = get_or_fail(db, Material, material_code)
    total, free = db.execute(
        select(
            func.coalesce(func.sum(Batch.quantity), 0),
            func.coalesce(func.sum(Batch.quantity - Batch.reserved), 0),
        ).where(Batch.material_code == material_code, Batch.status == "accepted")
    ).one()
    return {
        "code": material.code,
        "name": material.name,
        "unit": material.unit,
        "total": str(total),
        "free": str(free),
    }


def fifo(code: str) -> Select:
    return (
        select(Batch)
        .where(Batch.material_code == code, Batch.status == "accepted")
        .order_by(Batch.received_on, Batch.id)
        .with_for_update()
    )


def recipe_for_plan(db: Session, user: User, data: JobInput) -> Recipe:
    require(user, *PRODUCTION)
    return get_or_fail(db, Recipe, data.recipe_id)


def plan(db: Session, user: User, data: JobInput) -> dict:
    recipe = recipe_for_plan(db, user, data)
    ingredients = db.scalars(
        select(Ingredient).where(Ingredient.recipe_id == recipe.id).order_by(Ingredient.material_code)
    ).all()
    job = Job(**data.model_dump(), status="planned")
    db.add(job)
    db.flush()
    for ingredient in ingredients:
        code = ingredient.material_code
        need = ingredient.per_unit * data.quantity
        db.add(Component(job_id=job.id, material_code=code, required=need))
        for batch in db.scalars(fifo(code)):
            take = min(need, batch.quantity - batch.reserved)
            if take:
                before = serialize(batch)
                batch.reserved += take
                db.add(Allocation(job_id=job.id, batch_id=batch.id, reserved=take))
                audit(db, user, batch, before)
                need -= take
            if not need:
                break
        if need:
            job.status = "shortage"
    audit(db, user, job)
    return job_detail(db, user, job.id)


def allocations(db: Session, job_id: int) -> list[tuple[Allocation, Batch]]:
    rows = db.execute(
        select(Allocation, Batch)
        .join(Batch)
        .where(Allocation.job_id == job_id)
        .order_by(Batch.material_code, Batch.received_on, Batch.id)
    )
    return [(allocation, batch) for allocation, batch in rows]


def job_detail(db: Session, user: User, job_id: int) -> dict:
    require(user, *READERS)
    job = get_or_fail(db, Job, job_id)
    recipe = get_or_fail(db, Recipe, job.recipe_id)
    held: dict[str, Decimal] = defaultdict(Decimal)
    used: dict[str, Decimal] = defaultdict(Decimal)
    for allocation, batch in allocations(db, job_id):
        held[batch.material_code] += allocation.reserved
        used[batch.material_code] += allocation.consumed
    components = []
    rows = db.execute(
        select(Component, Material).join(Material).where(Component.job_id == job_id).order_by(Material.code)
    )
    for component, material in rows:
        code = material.code
        reserved = held[code]
        signal = "green" if reserved == component.required else "yellow" if reserved else "red"
        components.append(
            {
                "code": code,
                "name": material.name,
                "unit": material.unit,
                "required": str(component.required),
                "reserved": str(reserved),
                "consumed": str(used[code]),
                "missing": str(component.required - reserved),
                "signal": signal,
            }
        )
    return {**serialize(job), "recipe": recipe.name, "components": components}


def job_transition(db: Session, user: User, job_id: int, action: Literal["start", "cancel"]) -> dict:
    require(user, *PRODUCTION)
    job = get_or_fail(db, Job, job_id, lock=True)
    before = serialize(job)
    if action == "start":
        if job.status == "shortage":
            details = job_detail(db, user, job_id)
            missing = ", ".join(
                f"{c['missing']} {c['unit']} · {c['name']}"
                for c in details["components"]
                if Decimal(c["missing"])
            )
            raise DomainError(f"Не хватает сырья для запуска: {missing}")
        if job.status != "planned":
            raise DomainError("Запустить можно только запланированное задание")
        job.status = "running"
    else:
        if job.status not in ("planned", "shortage"):
            raise DomainError("Отмена доступна только до запуска")
        for allocation, row in allocations(db, job_id):
            batch = get_or_fail(db, Batch, row.id, lock=True)
            db.refresh(batch, with_for_update=True)
            before_batch = serialize(batch)
            batch.reserved -= allocation.reserved
            allocation.reserved = ZERO
            audit(db, user, batch, before_batch)
        job.status = "cancelled"
    audit(db, user, job, before)
    return job_detail(db, user, job_id)


def complete(db: Session, user: User, job_id: int, data: Completion, today: date) -> dict:
    require(user, *PRODUCTION)
    job = get_or_fail(db, Job, job_id, lock=True)
    if job.status != "running":
        raise DomainError("Завершить можно только запущенное задание")
    components = set(db.scalars(select(Component.material_code).where(Component.job_id == job_id)))
    if components != set(data.actual):
        raise DomainError("Укажите фактический расход для каждого компонента")
    own = {batch.id: line for line, batch in allocations(db, job_id)}
    for code, need in sorted(data.actual.items()):
        batches = list(db.scalars(fifo(code).execution_options(populate_existing=True)))
        for batch in batches:
            allocation = own.get(batch.id)
            if allocation and allocation.reserved:
                before = serialize(batch)
                used = min(allocation.reserved, need)
                batch.reserved -= allocation.reserved
                batch.quantity -= used
                allocation.reserved = ZERO
                allocation.consumed = used
                need -= used
                audit(db, user, batch, before)
        for batch in batches:
            if not need:
                break
            used = min(need, batch.quantity - batch.reserved)
            if used:
                before = serialize(batch)
                allocation = own.get(batch.id)
                if allocation is None:
                    allocation = Allocation(job_id=job_id, batch_id=batch.id, consumed=ZERO)
                    db.add(allocation)
                allocation.consumed += used
                batch.quantity -= used
                need -= used
                audit(db, user, batch, before)
        if need:
            material = get_or_fail(db, Material, code)
            raise DomainError(f"Не хватает {need} {material.unit} · {material.name}")
    before_job = serialize(job)
    job.status, job.completed_on = "completed", today
    audit(db, user, job, before_job)
    cost = job_cost(db, user, job_id)
    db.add(
        Outbox(
            job_id=job_id,
            payload={
                "job_id": job_id,
                "recipe_id": job.recipe_id,
                "quantity": str(job.quantity),
                "completed_on": today.isoformat(),
                "cost_rub": cost["total_rub"],
            },
        )
    )
    return job_detail(db, user, job_id)


def job_cost(db: Session, user: User, job_id: int) -> dict:
    require(user, *READERS)
    job = get_or_fail(db, Job, job_id)
    totals: dict[str, Decimal] = defaultdict(Decimal)
    actual = job.status == "completed"
    for allocation, batch in allocations(db, job_id):
        amount = allocation.consumed if actual else allocation.reserved
        totals[batch.material_code] += amount * batch.unit_cost
    return {
        "job_id": job_id,
        "kind": "actual" if actual else "reserved",
        "status": job.status,
        "total_rub": str(sum(totals.values(), ZERO)),
        "components_rub": {key: str(value) for key, value in totals.items()},
    }


def shortages(db: Session, user: User, period: Period) -> list[dict]:
    require(user, *READERS)
    jobs = db.scalars(
        select(Job.id)
        .where(Job.status == "shortage", Job.planned_on.between(period.start, period.end))
        .order_by(Job.planned_on, Job.id)
    )
    return [job_detail(db, user, job_id) for job_id in jobs]


def consumption(db: Session, user: User, period: Period, material_code: str) -> list[dict]:
    require(user, *READERS)
    get_or_fail(db, Material, material_code)
    rows = db.execute(
        select(Job.completed_on, func.sum(Allocation.consumed))
        .join(Allocation)
        .join(Batch)
        .where(
            Job.status == "completed",
            Batch.material_code == material_code,
            Job.completed_on.between(period.start, period.end),
        )
        .group_by(Job.completed_on)
        .order_by(Job.completed_on)
    )
    return [{"date": day.isoformat(), "quantity": str(quantity)} for day, quantity in rows]
