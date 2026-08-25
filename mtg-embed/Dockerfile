FROM python:3.12-slim

WORKDIR /app

# Copy only what's needed to install the package first, so dependency
# installs get cached in their own layer and aren't invalidated by every
# source change.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

ENTRYPOINT ["mtg-embed"]
CMD ["run", "--help"]
