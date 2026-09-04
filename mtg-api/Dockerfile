FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.lock ./
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.lock
COPY pyproject.toml ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/pip pip install --no-deps .

FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app/src ./src
COPY alembic.ini ./
COPY alembic ./alembic
ENV PYTHONPATH=/app/src
EXPOSE 8000
CMD alembic upgrade head && uvicorn mtg_api.main:app --host 0.0.0.0 --port 8000