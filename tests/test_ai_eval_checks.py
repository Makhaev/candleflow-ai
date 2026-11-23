import json
import sys

import pytest

from candleflow.config import settings
from scripts import run_ai_evals
from scripts.eval_checks import exact_values, has_amount, tool_check, verify_preview


def test_amounts_are_decimal_and_matched_to_specific_fields():
    case = {
        "kind": "stock",
        "expected": {"physical": "59.60", "free": "36.60", "reserved": "18"},
    }
    assert (
        exact_values(
            case, {"body": "Общий остаток 59,6 кг, свободно 36.600 кг, в резерве 18 кг."}, True
        ).status
        == "PASS"
    )
    assert (
        exact_values(case, {"body": "Общий остаток 36,6 кг, свободно 59,6 кг, в резерве 18 кг."}, True).status
        == "FAIL"
    )
    assert has_amount("Себестоимость задания: 1\u202f967,5328 RUB", "себестоимость задания", "1967.5328")
    assert not has_amount("Прибавка: 110.1601 RUB", "прибавка", "110.16")


def test_failed_tool_and_side_effect_do_not_pass():
    case = {"tools": ["code_interpreter"], "kind": "chart", "expected": {"2026-11-16": "1.8"}}
    answer = {
        "status": "complete",
        "tool_calls": [{"name": "code_interpreter", "status": "failed"}],
        "body": "",
    }
    assert tool_check(case, answer).status == "FAIL"
    assert exact_values(case, answer, True).status == "НЕ ВЫПОЛНЕНО"
    before = {"batches": {(1,): {"quantity": 5}}, "pending_actions": {}}
    after = {"batches": {(1,): {"quantity": 4}}, "pending_actions": {(2,): {"job_id": None}}}
    assert not verify_preview(before, after, {"id": 2})


def test_live_without_key_reports_not_run_and_does_not_call_provider(tmp_path, monkeypatch):
    target = tmp_path / "report.md"
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(sys, "argv", ["run_ai_evals.py", "--mode", "live", "--report", str(target)])

    def forbidden(*args, **kwargs):
        pytest.fail("A missing key should prevent evaluation")

    monkeypatch.setattr(run_ai_evals, "evaluate", forbidden)
    assert run_ai_evals.main() == 2
    report = target.read_text(encoding="utf-8")
    assert "Ключ OpenAI отсутствует" in report
    assert "Машинных результатов нет" in report
    assert "## Оценка LLM-судьёй" in report
    assert "PASS" not in report


def test_fixture_includes_every_requested_kind_and_confirm_runs_last():
    fixture = json.loads((run_ai_evals.EVALS / "cases.json").read_text(encoding="utf-8"))
    cases = fixture["cases"]
    assert len(cases) >= 10 and len({case["id"] for case in cases}) == len(cases)
    assert {case["kind"] for case in cases} >= {
        "stock",
        "shortage",
        "history",
        "cost",
        "confirm",
        "chart",
        "what-if",
        "csv",
        "unknown",
        "injection",
    }
    assert cases[-1]["kind"] == "confirm"
