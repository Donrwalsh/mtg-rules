FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock ./
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.lock
COPY mtg-ingestion ./mtg-ingestion
COPY mtg-embed ./mtg-embed
COPY pyproject.toml ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/pip pip install --no-deps ./mtg-ingestion ./mtg-embed .
CMD ["celery", "-A", "mtg_worker.celery_app", "worker", "--loglevel=info"]