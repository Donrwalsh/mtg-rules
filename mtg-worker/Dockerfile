FROM python:3.12-slim
WORKDIR /app
COPY mtg-ingestion ./mtg-ingestion
COPY mtg-embed ./mtg-embed
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ./mtg-ingestion ./mtg-embed .
CMD ["celery", "-A", "mtg_worker.celery_app", "worker", "--loglevel=info"]
