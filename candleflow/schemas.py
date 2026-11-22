from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Code = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_-]{0,31}$")]
Quantity = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=6)]
Nonnegative = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=6)]
Pieces = Annotated[Decimal, Field(gt=0, max_digits=9, decimal_places=0)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Input):
    login: Text
    password: Text


class MaterialInput(Input):
    code: Code
    name: Text
    unit: Text


class ReceiptLine(Input):
    material_code: Code
    quantity: Quantity
    unit_price: Nonnegative
    currency: Literal["RUB", "USD", "EUR"]
    comment: Annotated[str, Field(max_length=500)] = ""


class ReceiptInput(Input):
    supplier: Text
    received_on: date
    lines: Annotated[list[ReceiptLine], Field(min_length=1, max_length=100)]


class Inspection(Input):
    status: Literal["accepted", "rejected"]


class RecipeInput(Input):
    name: Text
    ingredients: Annotated[dict[Code, Quantity], Field(min_length=1, max_length=50)]


class JobInput(Input):
    recipe_id: Annotated[int, Field(gt=0)]
    quantity: Pieces
    planned_on: date


class Completion(Input):
    actual: Annotated[dict[Code, Nonnegative], Field(min_length=1, max_length=50)]


class Period(Input):
    start: date
    end: date

    @model_validator(mode="after")
    def valid_range(self):
        if self.end < self.start or (self.end - self.start).days > 366:
            raise ValueError("Укажите период до 367 дней")
        return self
