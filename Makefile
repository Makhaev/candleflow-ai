# Short commands for the Docker Compose workflow. Run `make` to list them.
COMPOSE := docker compose
PG := postgresql+psycopg://candleflow:candleflow@db:5432
# Mount the working tree so checks run against current code without a rebuild.
DEV_RUN := $(COMPOSE) run --rm -T --no-deps -v "$(CURDIR):/app" -e PYTHONDONTWRITEBYTECODE=1

.DEFAULT_GOAL := help
.PHONY: help up down logs reset test lint fix eval eval-live

help: ## Список команд
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  make %-10s %s\n", $$1, $$2}'

up: ## Собрать и поднять весь стек с демо-данными (UI: http://127.0.0.1:8080)
	$(COMPOSE) up --build -d
	@echo "UI: http://127.0.0.1:8080  ·  Swagger: http://127.0.0.1:8000/docs  ·  пароль demo-candleflow"

down: ## Остановить стек, данные сохраняются
	$(COMPOSE) down

logs: ## Логи API, worker и мока маркировки
	$(COMPOSE) logs -f --tail=100 api worker marking

reset: ## Удалить данные и заново залить демо-набор
	$(COMPOSE) down -v
	$(MAKE) up

test: ## pytest на отдельной базе candleflow_test
	$(COMPOSE) up -d db
	-$(COMPOSE) exec -T db createdb -U candleflow candleflow_test 2>/dev/null
	$(DEV_RUN) -e TEST_DATABASE_URL=$(PG)/candleflow_test api python -m pytest -q -p no:cacheprovider

lint: ## ruff, eslint и проверка сборки фронтенда
	$(DEV_RUN) api sh -c 'ruff check --no-cache . && ruff format --check --no-cache .'
	npm --prefix frontend ci --no-audit --no-fund
	npm --prefix frontend run lint
	npm --prefix frontend run build

fix: ## Автоисправления ruff
	$(DEV_RUN) api sh -c 'ruff check --fix --no-cache . && ruff format --no-cache .'

eval: ## Эталонный прогон ассистента в fake-режиме, отчёт в evals/latest_report.md
	$(COMPOSE) up -d db
	-$(COMPOSE) exec -T db createdb -U candleflow candleflow_eval 2>/dev/null
	$(DEV_RUN) -e EVAL_DATABASE_URL=$(PG)/candleflow_eval api python -m scripts.run_ai_evals --mode fake

eval-live: ## Эталонный прогон с OpenAI и LLM-судьёй (нужен OPENAI_API_KEY в .env)
	$(COMPOSE) up -d db
	-$(COMPOSE) exec -T db createdb -U candleflow candleflow_eval 2>/dev/null
	$(DEV_RUN) -e EVAL_DATABASE_URL=$(PG)/candleflow_eval api python -m scripts.run_ai_evals --mode live
