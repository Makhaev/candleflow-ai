FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY candleflow candleflow
RUN pip install --no-cache-dir '.[dev]'
COPY alembic.ini ./
COPY migrations migrations
COPY prompts prompts
COPY scripts scripts
COPY evals evals
COPY tests tests
CMD ["uvicorn", "candleflow.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
