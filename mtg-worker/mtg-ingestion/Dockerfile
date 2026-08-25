FROM python:3.12-slim

WORKDIR /app

# Copy only what's needed to install the package first, so dependency
# installs get cached in their own layer and aren't invalidated by every
# source change.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# data/ is meant to be a mounted volume (see docker-compose.yml) so raw
# downloads and parsed JSONL persist on the host -- this just makes sure a
# fresh container has somewhere to write if it's ever run without one.
RUN mkdir -p data/raw data/parsed

ENTRYPOINT ["mtg-ingest"]
CMD ["--help"]
