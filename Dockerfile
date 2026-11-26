FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml ./
COPY candleflow candleflow
# Dev extras stay in the image so tests and ruff run in the same environment as the API.
RUN pip install --no-cache-dir '.[dev]'
COPY alembic.ini ./
COPY migrations migrations
COPY prompts prompts
COPY scripts scripts
COPY evals evals
COPY tests tests
# UID 1000 matches the usual host user, so reports written to a bind mount stay editable.
RUN useradd --uid 1000 --create-home app
USER app
EXPOSE 8000
CMD ["uvicorn", "candleflow.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "*"]
