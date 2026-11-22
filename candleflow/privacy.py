import csv
import io
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from sqlalchemy import select
from sqlalchemy.orm import Session

from candleflow.auth import DomainError
from candleflow.models import Batch, User

LEGAL = r"(?:ооо|ао|пао|ип|зао|oao|llc|ltd)"
BOUNDARY = r"[^\w]{0,8}"
ENDINGS = r"(?:у|а|ою|ой|ом|ым|ым|е|ы|ой|ого|ой|ой|у|ой)?"
SAFE_HEADERS = {
    "date",
    "quantity",
    "qty",
    "price",
    "cost",
    "cost_rub",
    "week",
    "amount",
    "currency",
    "дата",
    "количество",
    "цена",
    "стоимость",
    "неделя",
    "сумма",
    "валюта",
    "расход",
}
NUMBER = re.compile(r"[+-]?\d{1,30}(?:[.,]\d{1,16})?")
ISODATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class Pseudonyms:
    def __init__(self, db: Session):
        self.rules: list[tuple[re.Pattern, str]] = []
        self.originals: dict[str, str] = {}
        for user_id, name in db.execute(select(User.id, User.full_name).order_by(User.id)):
            parts = re.findall(r"[\w-]+", name)
            if not parts:
                continue
            surname = re.escape(parts[0])
            patterns = [re.escape(name), surname + ENDINGS]
            if len(parts) > 1:
                first = re.escape(parts[1][0])
                full_first = re.escape(parts[1])
                root_first = re.escape(parts[1].rstrip("ая"))
                root_last = re.escape(parts[0].rstrip("ая"))
                surname_forms = rf"(?:{surname}{ENDINGS}|{root_last}(?:ой|ую|ою|ой|е))"
                first_forms = rf"(?:{full_first}{ENDINGS}|{root_first}(?:е|у|ой|ы|а))"
                patterns.insert(0, rf"{surname_forms}{BOUNDARY}(?:{first_forms}|{first}\.?)")
                patterns.insert(0, rf"{first_forms}{BOUNDARY}{surname_forms}")
            self.add(f"EMPLOYEE_{user_id}", name, "|".join(patterns))
        suppliers: dict[str, tuple[int, str]] = {}
        for name, first_id in db.execute(select(Batch.supplier, Batch.id).order_by(Batch.id)):
            tokens = [part for part in re.findall(r"\w+", name.casefold()) if not re.fullmatch(LEGAL, part)]
            if tokens:
                suppliers.setdefault(" ".join(tokens), (first_id, name))
        for canonical, (first_id, name) in sorted(suppliers.items(), key=lambda item: -len(item[0])):
            words = BOUNDARY.join(re.escape(token) for token in canonical.split())
            self.add(f"SUPPLIER_{first_id}", name, rf"(?:{LEGAL}{BOUNDARY})?{words}(?:{BOUNDARY}{LEGAL})?")

    def add(self, alias: str, original: str, pattern: str) -> None:
        self.rules.append((re.compile(rf"(?<!\w)(?:{pattern})(?!\w)", re.IGNORECASE), alias))
        self.originals[alias] = original

    def mask(self, value: Any) -> Any:
        if isinstance(value, str):
            value = unicodedata.normalize("NFKC", value)
            for pattern, alias in self.rules:
                value = pattern.sub(alias, value)
            return value
        if isinstance(value, dict):
            return {key: self.mask(item) for key, item in value.items() if key != "comment"}
        if isinstance(value, list):
            return [self.mask(item) for item in value]
        return value

    def restore(self, text: str) -> str:
        return re.sub(
            r"(?<!\w)(?:SUPPLIER|EMPLOYEE)_\d+(?!\w)",
            lambda match: self.originals.get(match[0], match[0]),
            text,
            flags=re.IGNORECASE,
        )


def sanitize_upload(filename: str, data: bytes, aliases: Pseudonyms) -> bytes:
    """Preserve numeric/date cells and pseudonymize all free-text categories."""
    try:
        if filename.lower().endswith(".xlsx"):
            with ZipFile(io.BytesIO(data)) as archive:
                if sum(entry.file_size for entry in archive.infolist()) > 8_000_000:
                    raise DomainError("Распакованный файл слишком велик", 413)
            workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            try:
                return sanitize_rows(workbook.active.values, aliases)
            finally:
                workbook.close()
        if filename.lower().endswith(".csv"):
            source = data.decode("utf-8-sig")
            delimiter = ";" if source.splitlines() and ";" in source.splitlines()[0] else ","
            return sanitize_rows(csv.reader(io.StringIO(source), delimiter=delimiter, strict=True), aliases)
    except (BadZipFile, InvalidFileException, ValueError, KeyError, csv.Error, UnicodeError) as exc:
        raise DomainError("Проверьте формат файла. Поддерживаются CSV UTF-8 и XLSX.", 422) from exc
    raise DomainError("Загрузите CSV или XLSX", 422)


def sanitize_rows(rows, aliases: Pseudonyms) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    categories: dict[str, str] = {}
    for index, row in enumerate(rows):
        if index >= 5001 or len(row) > 50:
            raise DomainError("Лимит файла: 5000 строк данных и 50 столбцов", 413)
        cells = []
        for column, raw in enumerate(row, start=1):
            value = raw.date().isoformat() if isinstance(raw, datetime) else str(raw or "")
            if raw == 0:
                value = "0"
            masked = aliases.mask(value.strip())
            if index == 0:
                cell = masked if masked.casefold() in SAFE_HEADERS else f"column_{column}"
            elif isinstance(raw, (date, Decimal)) or NUMBER.fullmatch(masked) or ISODATE.fullmatch(masked):
                cell = masked.lstrip("+")
            elif not masked or masked in aliases.originals or masked in {"RUB", "EUR", "USD"}:
                cell = masked
            else:
                cell = categories.setdefault(value, f"CATEGORY_{len(categories) + 1}")
            cells.append(cell)
        writer.writerow(cells)
    if not output.getvalue():
        raise DomainError("В файле нет строк", 422)
    return output.getvalue().encode("utf-8")
