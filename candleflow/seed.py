from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from candleflow import inventory as inv
from candleflow.api import today
from candleflow.auth import password_hash
from candleflow.db import SessionLocal
from candleflow.models import Batch, DemoSeedRun, Material, Recipe, User
from candleflow.schemas import Completion, JobInput, MaterialInput, ReceiptInput, ReceiptLine, RecipeInput

DEMO_PASSWORD = "demo-candleflow"
DEMO_VERSION = "inventory_examples_v2"
ACCOUNTS = (
    ("warehouse", "storekeeper", "Петров Иван"),
    ("technologist", "technologist", "Соколова Анна"),
    ("manager", "manager", "Орлова Елена"),
)
MATERIALS = (
    ("SOY", "Соевый воск", "кг", "40", "560", "RUB"),
    ("COCONUT", "Кокосовый воск", "кг", "30", "6", "USD"),
    ("BEES", "Пчелиный воск", "кг", "10", "8", "EUR"),
    ("LAV", "Лавандовая отдушка", "кг", "1", "24", "EUR"),
    ("VAN", "Ванильная отдушка", "кг", "3", "22", "USD"),
    ("CEDAR", "Кедровая отдушка", "кг", "2", "24", "USD"),
    ("WICK", "Хлопковый фитиль", "шт", "400", "8", "RUB"),
    ("JAR", "Стакан янтарный", "шт", "400", "65", "RUB"),
    ("LID", "Крышка", "шт", "400", "29", "RUB"),
    ("LABEL", "Этикетка", "шт", "500", "4", "RUB"),
    ("BOX", "Коробка", "шт", "300", "24", "RUB"),
)
RECIPES = (
    ("Лаванда", "SOY", "LAV"),
    ("Ваниль", "COCONUT", "VAN"),
    ("Кедр", "BEES", "CEDAR"),
)
PACKAGING = {code: Decimal(1) for code in ("WICK", "JAR", "LID", "LABEL", "BOX")}
Currency = Literal["RUB", "USD", "EUR"]
InspectionStatus = Literal["accepted", "rejected"]


def receive_lot(
    db: Session,
    manager: User,
    line: ReceiptLine,
    day: date,
    *,
    supplier: str = "ООО Восковой двор",
    status: InspectionStatus | None = "accepted",
) -> None:
    (batch,) = inv.receive(db, manager, ReceiptInput(supplier=supplier, received_on=day, lines=[line]))
    if status:
        inv.inspect_batch(db, manager, batch["id"], status)


def lot(code: str, quantity: str, price: str, currency: Currency, comment: str = "") -> ReceiptLine:
    return ReceiptLine(
        material_code=code,
        quantity=Decimal(quantity),
        unit_price=Decimal(price),
        currency=currency,
        comment=comment,
    )


def create_catalog(db: Session, day: date) -> tuple[User, dict[str, int]]:
    users = [
        User(login=login, role=role, full_name=name, password_hash=password_hash(DEMO_PASSWORD))
        for login, role, name in ACCOUNTS
    ]
    db.add_all(users)
    db.flush()
    manager = users[-1]
    for index, (code, name, unit, quantity, price, currency) in enumerate(MATERIALS):
        inv.add_material(db, manager, MaterialInput(code=code, name=name, unit=unit))
        receive_lot(
            db,
            manager,
            lot(code, quantity, price, currency),
            day - timedelta(days=91 - index),
            supplier=("ООО Восковой двор", "ООО Северный аромат")[index % 2],
        )
    ids = {}
    for name, wax, scent in RECIPES:
        ingredients = {**PACKAGING, wax: Decimal("0.18"), scent: Decimal("0.01")}
        ids[name] = inv.add_recipe(db, manager, RecipeInput(name=name, ingredients=ingredients))["id"]
    return manager, ids


def create_examples(db: Session, manager: User, day: date) -> None:
    oldest = db.scalar(select(func.min(Batch.received_on)).where(Batch.material_code == "SOY"))
    anchor = min(oldest or day, day - timedelta(days=91))
    receive_lot(
        db,
        manager,
        lot("SOY", "8", "510", "RUB", "FIFO · первая принятая партия"),
        anchor - timedelta(days=2),
    )
    receive_lot(
        db,
        manager,
        lot("SOY", "12", "530", "RUB", "FIFO · вторая принятая партия"),
        anchor - timedelta(days=1),
    )
    receive_lot(
        db,
        manager,
        lot("SOY", "2", "500", "RUB", "FIFO · ранняя партия забракована"),
        anchor - timedelta(days=3),
        status="rejected",
    )
    saturday = day - timedelta(days=(day.weekday() - 5) % 7 + 7)
    receive_lot(
        db,
        manager,
        lot("SOY", "3", "6", "USD", "Субботняя поставка · курс пятницы"),
        saturday,
        supplier="ООО Северный аромат",
        status=None,
    )


def create_demo_jobs(db: Session, manager: User, recipes: dict[str, int], day: date) -> None:
    for weeks, count in ((10, 8), (5, 12), (1, 10)):
        completed_on = day - timedelta(weeks=weeks)
        quantity = Decimal(count)
        job = inv.plan(
            db, manager, JobInput(recipe_id=recipes["Лаванда"], quantity=quantity, planned_on=completed_on)
        )
        inv.job_transition(db, manager, job["id"], "start")
        actual = {
            **dict.fromkeys(PACKAGING, quantity),
            "SOY": quantity * Decimal("0.18"),
            "LAV": quantity * Decimal("0.01"),
        }
        inv.complete(db, manager, job["id"], Completion(actual=actual), completed_on)
    inv.plan(db, manager, JobInput(recipe_id=recipes["Лаванда"], quantity=Decimal(100), planned_on=day))
    inv.plan(db, manager, JobInput(recipe_id=recipes["Ваниль"], quantity=Decimal(20), planned_on=day))


def legacy_demo(db: Session) -> tuple[User, dict[str, int]] | None:
    users = {
        user.login: user
        for user in db.scalars(select(User).where(User.login.in_([row[0] for row in ACCOUNTS])))
    }
    if any(
        login not in users or (users[login].role, users[login].full_name) != (role, name)
        for login, role, name in ACCOUNTS
    ):
        return None
    codes = set(db.scalars(select(Material.code)))
    if not codes.issuperset(row[0] for row in MATERIALS):
        return None
    recipes = dict(db.execute(select(Recipe.name, Recipe.id)).all())
    if not recipes.keys() >= {row[0] for row in RECIPES}:
        return None
    return users["manager"], recipes


def seed(day: date) -> None:
    with SessionLocal.begin() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(28531)"))
        if db.get(DemoSeedRun, DEMO_VERSION):
            return
        existing = legacy_demo(db)
        if existing:
            manager, recipes = existing
            create_examples(db, manager, day)
            receive_lot(
                db, manager, lot("LAV", "0.25", "24", "EUR", "Партия для примера частичного резерва"), day
            )
            inv.plan(
                db, manager, JobInput(recipe_id=recipes["Лаванда"], quantity=Decimal(100), planned_on=day)
            )
        elif any(db.scalar(select(model).limit(1)) is not None for model in (User, Material, Recipe)):
            return
        else:
            manager, recipes = create_catalog(db, day)
            create_examples(db, manager, day)
            create_demo_jobs(db, manager, recipes, day)
        db.add(DemoSeedRun(key=DEMO_VERSION, reference_day=day))


if __name__ == "__main__":
    seed(today())
