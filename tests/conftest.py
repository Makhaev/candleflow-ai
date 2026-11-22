import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

if url := os.getenv("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = url
os.environ["AI_PROVIDER"] = "fake"

from candleflow.auth import password_hash
from candleflow.db import SessionLocal, engine
from candleflow.models import Base, User


@pytest.fixture(scope="session")
def postgres():
    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("Set TEST_DATABASE_URL to a dedicated PostgreSQL test database")
    if not engine.url.database.endswith("_test"):
        pytest.fail("Use a dedicated database whose name ends in _test")
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture
def db(postgres):
    with SessionLocal.begin() as connection:
        names = ", ".join(Base.metadata.tables)
        connection.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        for role, name in (("manager", "Орлова Елена"), ("warehouse", "Петров Иван")):
            connection.add(
                User(login=role, role=role, full_name=name, password_hash=password_hash("testpass"))
            )
    with SessionLocal() as session:
        yield session
