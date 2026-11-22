from datetime import date, timedelta

from sqlalchemy import select, text

from candleflow import inventory as inv
from candleflow.api import today
from candleflow.auth import password_hash
from candleflow.db import SessionLocal
from candleflow.models import User
from candleflow.schemas import Completion, JobInput, MaterialInput, ReceiptInput, RecipeInput

DEMO_PASSWORD = "demo-candleflow"
MATERIALS = [
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
]


def seed(day: date):
    with SessionLocal.begin() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(28531)"))
        manager = db.scalar(select(User).where(User.login == "manager"))
        if manager:
            return
        users = [
            User(
                login="warehouse" if role == "storekeeper" else role,
                role=role,
                full_name=name,
                password_hash=password_hash(DEMO_PASSWORD),
            )
            for role, name in (
                ("storekeeper", "Петров Иван"),
                ("technologist", "Соколова Анна"),
                ("manager", "Орлова Елена"),
            )
        ]
        db.add_all(users)
        db.flush()
        manager = users[-1]
        for code, name, unit, *_ in MATERIALS:
            inv.add_material(db, manager, MaterialInput(code=code, name=name, unit=unit))
        for index, (code, _, _, quantity, price, currency) in enumerate(MATERIALS):
            (batch,) = inv.receive(
                db,
                manager,
                ReceiptInput.model_validate(
                    {
                        "supplier": ("ООО Восковой двор", "ООО Северный аромат")[index % 2],
                        "received_on": day - timedelta(days=56 - index),
                        "lines": [
                            {
                                "material_code": code,
                                "quantity": quantity,
                                "unit_price": price,
                                "currency": currency,
                            }
                        ],
                    }
                ),
            )
            inv.inspect_batch(db, manager, batch["id"], "accepted")
        base = {"WICK": "1", "JAR": "1", "LID": "1", "LABEL": "1", "BOX": "1"}
        for name, wax, scent in (
            ("Лаванда", "SOY", "LAV"),
            ("Ваниль", "COCONUT", "VAN"),
            ("Кедр", "BEES", "CEDAR"),
        ):
            inv.add_recipe(
                db,
                manager,
                RecipeInput.model_validate(
                    {
                        "name": name,
                        "ingredients": {**base, wax: "0.18", scent: "0.01"},
                    }
                ),
            )
        for weeks in (5, 3, 1):
            historical = day - timedelta(weeks=weeks)
            job = inv.plan(db, manager, JobInput(recipe_id=1, quantity=10, planned_on=historical))
            inv.job_transition(db, manager, job["id"], "start")
            inv.complete(
                db,
                manager,
                job["id"],
                Completion.model_validate(
                    {
                        "actual": {**{code: "10" for code in base}, "SOY": "1.8", "LAV": "0.10"},
                    }
                ),
                historical,
            )
        inv.plan(db, manager, JobInput(recipe_id=1, quantity=100, planned_on=day))
        inv.plan(db, manager, JobInput(recipe_id=2, quantity=20, planned_on=day))


if __name__ == "__main__":
    seed(today())
