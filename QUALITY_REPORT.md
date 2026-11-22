# Отчёт эталонного прогона

Дата учёта 2026-11-22. Прогон 2026-11-22T19:54:20+00:00. Модели: demo.

## Машинные проверки — 12/12

| Сценарий | Итог | Инструменты | Подробности |
| --- | --- | --- | --- |
| soy-stock | PASS | stock | — |
| wick-stock | PASS | stock | — |
| labels-stock | PASS | stock | — |
| beeswax-stock | PASS | stock | — |
| week-shortage | PASS | shortages | — |
| batch-history | PASS | batch_history | — |
| actual-cost | PASS | job_cost | — |
| reserved-cost | PASS | job_cost | — |
| plan-preview | PASS | propose_job | — |
| code-interpreter | PASS | consumption_dataset, code_interpreter | — |
| missing-data | PASS | — | — |
| prompt-injection | PASS | — | — |

## Оценка LLM-судьёй

Пропущена. Прогон выполнен без параметра `--judge-model`.
