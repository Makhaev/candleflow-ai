"""Per-user assistant memory. Every query is filtered by the owner, so other users' facts never load."""

from typing import Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from candleflow.auth import DomainError
from candleflow.chat_models import Memory
from candleflow.models import User
from candleflow.schemas import Input, Text

CURRENCIES = {"RUB", "EUR", "USD"}


class Preference(Input):
    key: Literal["currency", "workshop"]
    value: Text


def validate(data: Preference) -> Preference:
    if data.key == "currency" and data.value not in CURRENCIES:
        raise DomainError("Валюта должна быть RUB, EUR или USD", 422)
    return data


def preferences(db: Session, user_id: int) -> list[Memory]:
    return list(db.scalars(select(Memory).where(Memory.user_id == user_id).order_by(Memory.id)))


def preferred_currency(db: Session, user_id: int) -> str:
    return next((row.value for row in preferences(db, user_id) if row.key == "currency"), "RUB")


def save_preference(db: Session, user: User, data: Preference) -> dict:
    validate(data)
    statement = insert(Memory).values(user_id=user.id, key=data.key, value=data.value)
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[Memory.user_id, Memory.key], set_={"value": statement.excluded.value}
        )
    )
    return {"preference": data.model_dump()}
