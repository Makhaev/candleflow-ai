from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from candleflow import inventory as inv
from candleflow.db import SessionLocal
from candleflow.models import (
    Allocation,
    Audit,
    Batch,
    DemoSeedRun,
    Job,
    Material,
    Recipe,
    StockMovement,
    User,
)
from candleflow.schemas import Period
from candleflow.seed import DEMO_VERSION, create_catalog, create_demo_jobs, seed

DAY = date(2026, 11, 23)
TRACKED = (User, Material, Recipe, Batch, Job, Allocation, Audit, StockMovement, DemoSeedRun)


def counts():
    with SessionLocal() as db:
        return {model: db.scalar(select(func.count()).select_from(model)) for model in TRACKED}


def test_demo_seed_is_concurrent_safe_and_covers_scenarios(empty_db):
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(seed, [DAY, DAY]))
    original = counts()
    seed(DAY + timedelta(days=7))
    assert counts() == original
    assert {model: original[model] for model in (Material, Recipe, Batch, Job, User, DemoSeedRun)} == {
        Material: 11,
        Recipe: 3,
        Batch: 15,
        Job: 5,
        User: 3,
        DemoSeedRun: 1,
    }
    with SessionLocal() as db:
        assert db.get(DemoSeedRun, DEMO_VERSION).reference_day == DAY
        assert dict(db.execute(select(User.login, User.role)).all()) == {
            "warehouse": "storekeeper",
            "technologist": "technologist",
            "manager": "manager",
        }
        batches = list(db.scalars(select(Batch)))
        assert {batch.status for batch in batches} == {"accepted", "rejected", "on_review"}
        assert {batch.quote.currency for batch in batches} == {"RUB", "USD", "EUR"}
        (saturday,) = [batch for batch in batches if batch.status == "on_review"]
        assert saturday.quote.currency == "USD"
        assert saturday.received_on.weekday() == 5
        assert saturday.quote.quoted_on == saturday.received_on - timedelta(days=1)
        manager = db.scalar(select(User).where(User.role == "manager"))
        (shortage,) = inv.shortages(db, manager, Period(start=DAY, end=DAY))
        lavender = next(row for row in shortage["components"] if row["code"] == "LAV")
        assert Decimal(lavender["reserved"]) == Decimal("0.70")
        assert Decimal(lavender["missing"]) == Decimal("0.30")
        assert lavender["signal"] == "yellow"
        wax = [
            (allocation, batch)
            for allocation, batch in inv.allocations(db, shortage["id"])
            if batch.material_code == "SOY"
        ]
        assert [row.reserved for row, _ in wax] == [Decimal("2.60"), Decimal(12), Decimal("3.40")]
        assert [batch.received_on for _, batch in wax] == sorted({batch.received_on for _, batch in wax})
        assert wax[-1][1].id < wax[0][1].id
        assert all(batch.status == "accepted" for _, batch in wax)
        values = inv.consumption(db, manager, Period(start=DAY - timedelta(days=90), end=DAY), "SOY")
        assert [row["date"] for row in values] == [str(DAY - timedelta(weeks=i)) for i in (10, 5, 1)]
        assert [Decimal(row["quantity"]) for row in values] == [
            Decimal("1.44"),
            Decimal("2.16"),
            Decimal("1.8"),
        ]
        history = inv.batch_history(db, manager, wax[0][1].id)["history"]
        assert history[0]["after"]["status"] == "on_review"
        assert any(row["after"]["status"] == "accepted" for row in history[1:])


def test_legacy_demo_is_enriched_without_replacing_reservations(empty_db):
    with SessionLocal.begin() as db:
        manager, recipes = create_catalog(db, DAY)
        create_demo_jobs(db, manager, recipes, DAY)
        old = db.get(Allocation, (4, 1))
        before = (old.reserved, old.issued, old.consumed, old.state)
    upgrade_day = DAY + timedelta(weeks=1)
    seed(upgrade_day)
    after = counts()
    seed(upgrade_day + timedelta(days=1))
    assert counts() == after
    assert after[User] == 3 and after[Recipe] == 3
    assert after[Batch] == 16 and after[Job] == 6
    with SessionLocal() as db:
        old = db.get(Allocation, (4, 1))
        assert (old.reserved, old.issued, old.consumed, old.state) == before
        manager = db.scalar(select(User).where(User.role == "manager"))
        (job,) = inv.shortages(db, manager, Period(start=upgrade_day, end=upgrade_day))
        assert any(row["signal"] == "yellow" for row in job["components"])
        assert sum(batch.material_code == "SOY" for _, batch in inv.allocations(db, job["id"])) == 2


def test_seed_leaves_non_demo_database_untouched(db):
    before = counts()
    seed(DAY)
    assert counts() == before
