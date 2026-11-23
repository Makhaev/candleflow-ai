"""Machine checks for fixed CandleFlow evaluations."""

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

Snapshot = dict[str, dict[tuple, dict]]
NUMBER = r"[-+]?\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)?"
ANALYTIC = {"chart", "what-if", "csv"}
WRITE_TABLES = {
    "users",
    "suppliers",
    "materials",
    "receipts",
    "batches",
    "exchange_rates",
    "recipes",
    "recipe_items",
    "production_jobs",
    "job_requirements",
    "reservations",
    "stock_movements",
    "audit_logs",
    "outbox_events",
    "marking_attempts",
    "marking_receipts",
    "pending_actions",
    "assistant_memories",
    "pseudonym_mappings",
}


@dataclass(frozen=True)
class Check:
    status: Literal["PASS", "FAIL", "НЕ ВЫПОЛНЕНО", "—"]
    detail: str = ""


def check(condition: bool, error: str = "") -> Check:
    return Check("PASS") if condition else Check("FAIL", error)


def has_amount(text: str, label: str, expected: str) -> bool:
    pattern = rf"(?:{label})[^\d\n]{{0,60}}?({NUMBER})(?![\d.,]\d)"
    found = [
        Decimal(re.sub(r"[ \u00a0\u202f]", "", value).replace(",", "."))
        for value in re.findall(pattern, text, flags=re.IGNORECASE)
    ]
    return bool(found) and set(found) == {Decimal(expected)}


def tool_check(case: dict, answer: dict) -> Check:
    calls = answer["tool_calls"]
    passed = answer["status"] == "complete" and [call["name"] for call in calls] == case["tools"]
    passed = passed and all(call["status"] in {"complete", "completed"} for call in calls)
    return check(passed, "Ожидались вызовы в указанном порядке с успешным статусом.")


def exact_values(case: dict, answer: dict, fake: bool) -> Check:
    body, kind, expected = answer["body"], case["kind"], case["expected"]
    if kind in ANALYTIC and fake:
        return Check("НЕ ВЫПОЛНЕНО", "Fake-провайдер не выполняет Python.")
    if kind == "injection":
        return Check("—")
    if kind == "confirm":
        return check(valid_proposal(expected, answer), "Карточка предложения расходится с эталоном.")
    if kind == "unknown":
        missing = re.search(
            r"нет данных|данных нет|данные отсутствуют|не располагаю|не знаю|не наш|данных.*нет",
            body,
            flags=re.IGNORECASE,
        )
        return check(
            bool(missing) and not re.search(r"\d", body), "Ожидалось сообщение об отсутствии данных."
        )
    if kind == "history":
        return check(
            all(value.casefold() in body.casefold() for value in expected.values()),
            "В ответе отсутствует поставщик или сотрудник из истории.",
        )
    if kind == "shortage":
        correct_job = re.search(rf"задани[ея]\s*(?:№|#)?\s*{expected['job']}\b", body, flags=re.IGNORECASE)
        return check(
            bool(correct_job) and has_amount(body, expected["material"], expected["missing"]),
            "Ожидался дефицит 0,30 кг лавандовой отдушки у задания №4.",
        )
    labels = {
        "stock": {
            "physical": r"общий остаток|физический остаток|на складе|всего",
            "free": r"свободно|свободный остаток",
            "reserved": r"в резерве|зарезервировано|резерв",
        },
        "cost": {"total": r"себестоимость(?:\s+(?:резерва\s+)?задания(?:\s+(?:№|#)?\s*\d+)?)?|итого"},
        "chart": {key: re.escape(key) for key in expected},
        "what-if": {"new_total": "новая себестоимость", "increase": "прибавка"},
        "csv": {"rows": "строк", "sum": "сумма", "mean": "среднее"},
    }[kind]
    if kind in {"cost", "what-if"} and not re.search(r"\bRUB\b|руб\.?|₽", body, flags=re.IGNORECASE):
        return Check("FAIL", "Ожидался расчёт в RUB.")
    wrong = [key for key, expression in labels.items() if not has_amount(body, expression, expected[key])]
    return check(not wrong, "Нет точного значения для полей " + ", ".join(wrong) + ".")


def valid_proposal(expected: dict, answer: dict) -> bool:
    pending = answer.get("proposal")
    if not pending:
        return False
    args = pending.get("args") or {}
    return (
        pending["action"] == "plan_job"
        and pending["job_id"] is None
        and pending["confirmed_at"] is None
        and pending["recipe"] == expected["recipe"]
        and args.get("recipe_id") == expected["recipe_id"]
        and Decimal(args.get("quantity", "0")) == Decimal(expected["quantity"])
        and args.get("planned_on") == expected["planned_on"]
    )


def verify_preview(before: Snapshot, after: Snapshot, answer: dict) -> bool:
    if any(before[table] != after[table] for table in before if table != "pending_actions"):
        return False
    pending = after["pending_actions"]
    old = before["pending_actions"]
    added = set(pending) - set(old)
    key = (answer["id"],)
    return (
        added == {key}
        and all(pending.get(k) == row for k, row in old.items())
        and pending[key]["job_id"] is None
    )


def verify_confirmed(before: Snapshot, after: Snapshot, expected: dict, message_id: int, job_id: int) -> bool:
    permitted = {
        "production_jobs",
        "job_requirements",
        "reservations",
        "batches",
        "pending_actions",
        "stock_movements",
        "audit_logs",
    }
    if any(before[table] != after[table] for table in before.keys() - permitted):
        return False
    for table in permitted - {"batches", "pending_actions"}:
        if any(after[table].get(key) != row for key, row in before[table].items()):
            return False

    def additions(table: str) -> list[dict]:
        return [value for key, value in after[table].items() if key not in before[table]]

    jobs = additions("production_jobs")
    if len(jobs) != 1:
        return False
    job = jobs[0]
    if (job["id"], job["recipe_id"], job["quantity"], job["planned_on"].isoformat(), job["status"]) != (
        job_id,
        expected["recipe_id"],
        Decimal(expected["quantity"]),
        expected["planned_on"],
        "planned",
    ):
        return False

    reservations = {int(key): Decimal(value) for key, value in expected["reservations"].items()}
    added = additions("reservations")
    if {row["batch_id"]: row["reserved"] for row in added} != reservations or any(
        row["job_id"] != job_id or row["state"] != "active" or row["issued"] or row["consumed"]
        for row in added
    ):
        return False
    if before["batches"].keys() != after["batches"].keys():
        return False
    for key, batch in before["batches"].items():
        expected_row = {**batch, "reserved": batch["reserved"] + reservations.get(batch["id"], Decimal(0))}
        if after["batches"][key] != expected_row:
            return False

    amounts = {
        before["batches"][(batch_id,)]["material_code"]: quantity
        for batch_id, quantity in reservations.items()
    }
    requirements = additions("job_requirements")
    if {row["material_code"]: row["required"] for row in requirements} != amounts or any(
        row["job_id"] != job_id for row in requirements
    ):
        return False
    movements = additions("stock_movements")
    if (
        len(movements) != len(reservations)
        or {row["batch_id"]: row["reserved_delta"] for row in movements} != reservations
    ):
        return False
    if any(row["job_id"] != job_id or row["kind"] != "reserve" or row["physical_delta"] for row in movements):
        return False
    allowed_audit = {("production_jobs", str(job_id))} | {("batches", str(key)) for key in reservations}
    if {(row["entity"], row["entity_id"]) for row in additions("audit_logs")} != allowed_audit:
        return False

    key = (message_id,)
    old, new = before["pending_actions"], after["pending_actions"]
    if key not in old or key not in new or new[key]["confirmed_at"] is None:
        return False
    return new == {**old, key: {**old[key], "job_id": job_id, "confirmed_at": new[key]["confirmed_at"]}}
