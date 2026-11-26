"""Run the fixed, isolated assistant evaluation manually."""

import argparse
import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Annotated
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import create_engine, make_url, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[1]
EVALS = ROOT / "evals"


class Judgment(BaseModel):
    clarity: Annotated[int, Field(ge=0, le=2)]
    completeness: Annotated[int, Field(ge=0, le=2)]
    rationale: str


@dataclass
class Result:
    key: str
    checks: dict = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)
    model: str | None = None
    judge: Judgment | None = None
    judge_error: str = ""


@asynccontextmanager
async def isolated_database(dsn: str):
    from candleflow import db

    url = make_url(dsn)
    if url.get_backend_name() != "postgresql" or not (url.database or "").endswith("_eval"):
        raise ValueError("EVAL_DATABASE_URL должен указывать на PostgreSQL-базу с суффиксом _eval.")
    schema = "assistant_eval_" + uuid4().hex
    admin = create_engine(url.set(drivername="postgresql+psycopg"), hide_parameters=True)
    sync = create_engine(
        admin.url,
        connect_args={"options": f"-c search_path={schema}"},
        hide_parameters=True,
    )
    asynchronous = create_async_engine(
        url.set(drivername="postgresql+asyncpg"),
        connect_args={"server_settings": {"search_path": schema}},
        hide_parameters=True,
    )
    original_sync = db.SessionLocal.kw["bind"]
    original_async = db.AsyncSessionLocal.kw["bind"]
    created = False
    try:
        with admin.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {schema}"))
        created = True
        db.SessionLocal.configure(bind=sync)
        db.AsyncSessionLocal.configure(bind=asynchronous)
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        with patch.object(db, "engine", sync):
            command.upgrade(config, "head")
        yield
    finally:
        db.SessionLocal.configure(bind=original_sync)
        db.AsyncSessionLocal.configure(bind=original_async)
        await asynchronous.dispose()
        sync.dispose()
        try:
            if created:
                with admin.begin() as connection:
                    connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        finally:
            admin.dispose()


def snapshot() -> dict:
    from candleflow.db import Base, SessionLocal
    from scripts.eval_checks import WRITE_TABLES

    result = {}
    with SessionLocal() as db:
        for name in sorted(WRITE_TABLES):
            table = Base.metadata.tables[name]
            keys = [column.key for column in table.primary_key]
            rows = db.execute(select(table)).mappings()
            result[name] = {tuple(row[key] for key in keys): dict(row) for row in rows}
    return result


async def send(client: httpx.AsyncClient, case: dict) -> dict:
    dialog = (await client.post("/api/conversations")).raise_for_status().json()
    files = None
    if attachment := case.get("attachment"):
        files = {"upload": (attachment, (EVALS / attachment).read_bytes(), "text/csv")}
    async with client.stream(
        "POST",
        f"/api/conversations/{dialog['id']}/stream",
        data={"question": case["question"]},
        files=files,
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                event = json.loads(line[6:])
                if event["type"] == "done":
                    return event["message"]
    raise RuntimeError("Событие done отсутствует.")


async def files_check(client, case, answer, fake: bool):
    from scripts.eval_checks import ANALYTIC, Check, check

    if case["kind"] not in ANALYTIC:
        return Check("—")
    if not answer["code"]:
        return Check("FAIL", "Нет сохранённого кода.")
    if case["kind"] != "chart" and not fake:
        return Check("PASS")
    for file in answer["files"]:
        response = await client.get(f"/api/files/{file['id']}")
        if not response.is_success or not response.content:
            continue
        if fake or (file["mime"] == "image/png" and response.content.startswith(b"\x89PNG\r\n\x1a\n")):
            if fake:
                return Check(
                    "НЕ ВЫПОЛНЕНО",
                    "Демофайл скачивается. Выполнение кода и нативный результат не проверялись.",
                )
            return Check("PASS")
    return check(False, "Демоартефакт недоступен." if fake else "PNG не создан или не скачивается.")


async def changes_check(client, case, answer, before):
    from scripts.eval_checks import check, valid_proposal, verify_confirmed, verify_preview

    preview = snapshot()
    if case["kind"] != "confirm":
        return check(
            preview == before and not answer.get("proposal"),
            "Без подтверждения появились изменения в учёте, памяти или ожидающих действиях.",
        )
    if not verify_preview(before, preview, answer) or not valid_proposal(case["expected"], answer):
        return check(False, "До подтверждения ожидалось только одно предложение с точными параметрами.")
    path = f"/api/messages/{answer['id']}/confirm"
    first = await client.post(path)
    if not first.is_success:
        return check(False, f"Подтверждение вернуло HTTP {first.status_code}.")
    job_id = first.json()["id"]
    accepted = snapshot()
    if not verify_confirmed(preview, accepted, case["expected"], answer["id"], job_id):
        return check(False, "Записанное задание, резервы или движения расходятся с эталоном.")
    repeat = await client.post(path)
    return check(
        repeat.is_success and repeat.json()["id"] == job_id and snapshot() == accepted,
        "Повторное подтверждение не вернуло прежнее задание или изменило данные.",
    )


async def score(judge: AsyncOpenAI, model: str, case: dict, answer: dict, result: Result, aliases) -> None:
    prompt = (ROOT / "prompts" / "judge.md").read_text(encoding="utf-8")
    data = aliases.mask(
        {
            "question": case["question"],
            "answer": answer["body"],
            "expectation": case["expected"],
            "tool_names": result.tools,
            "files": [file["mime"] for file in answer["files"]],
            "has_confirmation_card": bool(answer.get("proposal")),
            "machine_checks": {key: value.status for key, value in result.checks.items()},
        }
    )
    try:
        parsed = await judge.responses.parse(
            model=model,
            instructions=prompt,
            input=json.dumps(data, ensure_ascii=False),
            text_format=Judgment,
            store=False,
        )
        result.judge = parsed.output_parsed
        if result.judge is None:
            result.judge_error = "Судья не вернул структурированную оценку."
    except (OpenAIError, ValidationError, TimeoutError) as exc:
        result.judge_error = f"Оценка не выполнена ({type(exc).__name__})."


async def evaluate(dsn: str, day: date, cases: list[dict], fake: bool, judge_model: str) -> list[Result]:
    from candleflow import api, chat_api
    from candleflow.config import settings
    from candleflow.db import SessionLocal
    from candleflow.main import app
    from candleflow.privacy import Pseudonyms
    from candleflow.rate_limit import limiter
    from candleflow.seed import DEMO_PASSWORD, seed
    from scripts.eval_checks import Check, exact_values, tool_check

    results = []
    async with isolated_database(dsn):
        seed(day)
        with SessionLocal() as db:
            aliases = Pseudonyms(db)
        judge = None if fake else AsyncOpenAI(api_key=settings.openai_api_key, timeout=40, max_retries=0)
        try:
            with (
                patch.object(settings, "ai_provider", "fake" if fake else "openai"),
                patch.object(api, "today", lambda: day),
                patch.object(chat_api, "today", lambda: day),
                # A manual run may be repeated within a minute; limits protect users, not the eval.
                patch.object(limiter, "enabled", False),
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://eval",
                    trust_env=False,
                ) as client:
                    login = await client.post(
                        "/api/login", json={"login": "manager", "password": DEMO_PASSWORD}
                    )
                    login.raise_for_status()
                    if login.json()["today"] != day.isoformat():
                        raise RuntimeError("Дата фиксированной демобазы не совпадает с датой API.")
                    for case in cases:
                        result = Result(case["id"])
                        results.append(result)
                        before = snapshot()
                        try:
                            answer = await send(client, case)
                            result.tools, result.model = answer["tools"], answer["model"]
                            result.checks["Инструменты"] = tool_check(case, answer)
                            result.checks["Точные результаты"] = exact_values(case, answer, fake)
                            result.checks["Код и файл"] = await files_check(client, case, answer, fake)
                            result.checks["Действия"] = await changes_check(client, case, answer, before)
                            if judge:
                                await score(judge, judge_model, case, answer, result, aliases)
                        except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as exc:
                            result.checks["Выполнение"] = Check(
                                "FAIL", f"Сценарий прервался ({type(exc).__name__})."
                            )
                            result.judge_error = "Сценарий прервался."
        finally:
            if judge:
                await judge.close()
    return results


def cell(value: str) -> str:
    return " ".join(value.split()).replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")


def report(
    target: Path,
    day: date,
    cases: list[dict],
    results: list[Result],
    mode: str,
    live_status: str,
    judge_model: str,
    problem: str = "",
) -> None:
    moment = datetime.now(ZoneInfo("Europe/Moscow")).isoformat(timespec="seconds")
    columns = ("Инструменты", "Точные результаты", "Код и файл", "Действия")
    failed = sum(any(check.status == "FAIL" for check in row.checks.values()) for row in results)
    partial = sum(
        any(check.status == "НЕ ВЫПОЛНЕНО" for check in row.checks.values())
        and all(check.status != "FAIL" for check in row.checks.values())
        for row in results
    )
    passed = len(results) - failed - partial
    lines = [
        "# Последний эталонный прогон ассистента",
        "",
        f"Дата демо-набора {day}. Время прогона {moment}. Режим `{mode}`.",
        "",
        f"**Живой прогон** — {live_status}",
        "",
        "## Машинные проверки",
        "",
    ]
    if results:
        models = sorted({row.model for row in results if row.model})
        lines += ["Модели ответов — " + (", ".join(models) or "недоступны") + ".", ""]
    if problem:
        lines += [problem, ""]
    if results:
        lines += [
            f"Полностью прошли **{passed}/{len(cases)}**. Частично проверены **{partial}**. Ошибок **{failed}**.",
            "",
            "| Сценарий | Инструменты | Точные результаты | Код и файл | Действия | Фактические вызовы и примечания |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for row in results:
            values = [row.checks[key].status if key in row.checks else "НЕ ВЫПОЛНЕНО" for key in columns]
            details = " · ".join(
                [", ".join(row.tools) or "Вызовов нет"]
                + [value.detail for value in row.checks.values() if value.detail]
            )
            lines.append("| " + " | ".join([row.key, *values, cell(details)]) + " |")
    else:
        lines += ["**НЕ ВЫПОЛНЕНО.** Машинных результатов нет."]
    lines += ["", "## Оценка LLM-судьёй", ""]
    if mode != "live" or not results:
        lines += ["**НЕ ВЫПОЛНЕНО.** Судья оценивает только ответы живого прогона."]
    else:
        lines += [
            f"Модель судьи `{judge_model}`. Ясность и полнота оцениваются отдельно по шкале от 0 до 2.",
            "",
            "| Сценарий | Ясность | Полнота | Комментарий |",
            "| --- | --- | --- | --- |",
        ]
        for row in results:
            if row.judge:
                lines.append(
                    f"| {row.key} | {row.judge.clarity} | {row.judge.completeness} | {cell(row.judge.rationale)} |"
                )
            else:
                lines.append(f"| {row.key} | — | — | {cell(row.judge_error or 'НЕ ВЫПОЛНЕНО')} |")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from candleflow.config import settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fake", "live"), default="fake")
    parser.add_argument("--judge-model", default=settings.fallback_model)
    parser.add_argument("--report", type=Path, default=EVALS / "latest_report.md")
    args = parser.parse_args()
    data = json.loads((EVALS / "cases.json").read_text(encoding="utf-8"))
    day, cases = date.fromisoformat(data["reference_day"]), data["cases"]
    if args.mode == "live" and not settings.openai_api_key:
        report(
            args.report,
            day,
            cases,
            [],
            args.mode,
            "**НЕ ВЫПОЛНЕН**. Ключ OpenAI отсутствует.",
            args.judge_model,
        )
        print("Live eval not run: OPENAI_API_KEY is missing.")
        return 2
    dsn = os.getenv("EVAL_DATABASE_URL")
    live_status = "выполнен, результаты ниже."
    if args.mode == "fake":
        live_status = (
            "**НЕ ВЫПОЛНЕН**. Ключ OpenAI отсутствует."
            if not settings.openai_api_key
            else "**НЕ ВЫПОЛНЕН**. Выбран режим fake."
        )
    try:
        if not dsn:
            raise ValueError("Укажите EVAL_DATABASE_URL для отдельной PostgreSQL-базы.")
        results = asyncio.run(evaluate(dsn, day, cases, args.mode == "fake", args.judge_model))
    except (
        CommandError,
        SQLAlchemyError,
        httpx.HTTPError,
        OpenAIError,
        RuntimeError,
        ValueError,
        OSError,
        ExceptionGroup,
    ) as exc:
        problem = (
            str(exc) if isinstance(exc, ValueError) and not dsn else f"Прогон прерван ({type(exc).__name__})."
        )
        if args.mode == "live":
            live_status = "**НЕ ВЫПОЛНЕН**. Прогон прервался."
        report(args.report, day, cases, [], args.mode, live_status, args.judge_model, problem)
        print(problem)
        return 2
    report(args.report, day, cases, results, args.mode, live_status, args.judge_model)
    print(f"Report written to {args.report}")
    return int(
        any(
            any(check.status == "FAIL" for check in result.checks.values())
            or (args.mode == "live" and result.judge is None)
            for result in results
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
