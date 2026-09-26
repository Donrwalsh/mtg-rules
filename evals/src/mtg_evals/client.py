from __future__ import annotations

import time
from pathlib import Path

import httpx

from mtg_evals.cases import Case, CaseFileError, load_cases
from mtg_evals.judge import JudgeConfig


class ApiError(Exception):
    pass


class PreflightError(Exception):
    pass


class ApiClient:
    def __init__(self, base_url: str, http: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        # Full-mode generation on a local model can take minutes.
        self._http = http or httpx.Client(timeout=600.0)

    def _get(self, path: str) -> httpx.Response:
        return self._http.get(f"{self.base_url}{path}")

    def health(self) -> dict:
        response = self._get("/health")
        response.raise_for_status()
        return response.json()

    def config(self) -> httpx.Response:
        return self._get("/api/v1/config")

    def query(self, question: str, *, generate: bool, overrides: dict) -> tuple[dict, float]:
        """(response body, latency in ms). Always tagged source="eval" so the
        run stays out of query history."""
        started = time.perf_counter()
        response = self._http.post(
            f"{self.base_url}/api/v1/query",
            json={
                "query": question,
                "generate": generate,
                "overrides": overrides,
                "source": "eval",
            },
        )
        latency_ms = (time.perf_counter() - started) * 1000
        if response.status_code != 200:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = response.text[:200]
            raise ApiError(f"HTTP {response.status_code}: {detail}")
        return response.json(), latency_ms


def preflight(
    api: ApiClient, eval_path: Path, mode: str, judge: JudgeConfig | None
) -> tuple[list[Case], dict]:
    """Fail fast, with a hint, before spending time on a run that can't work.
    Returns the loaded cases and the API's /api/v1/config body."""
    try:
        cases = load_cases(eval_path)
    except CaseFileError as exc:
        listed = "\n  ".join(exc.errors[:20])
        raise PreflightError(f"{eval_path} failed schema validation:\n  {listed}") from exc

    try:
        health = api.health()
    except httpx.HTTPError as exc:
        raise PreflightError(
            f"API not reachable at {api.base_url} ({exc.__class__.__name__}). "
            "Is `docker compose up` running?"
        ) from exc
    if health.get("qdrant") != "ok":
        raise PreflightError(f"/health reports qdrant={health.get('qdrant')!r}; is Qdrant up?")

    response = api.config()
    if response.status_code == 404:
        raise PreflightError(
            "/api/v1/config returned 404: eval mode is off. Set MTG_API_EVAL_MODE=true in .env "
            "and recreate the API: `docker compose up -d --build backend`."
        )
    if response.status_code != 200:
        raise PreflightError(f"/api/v1/config returned HTTP {response.status_code}")
    config = response.json()

    collection = config.get("collection", {})
    if not collection.get("points_count"):
        detail = collection.get("error") or "0 points"
        raise PreflightError(
            f"Qdrant collection {collection.get('name')!r} is empty or unreadable ({detail}). "
            "Run the embed pipeline first."
        )

    if mode == "full" and not (judge and judge.model):
        raise PreflightError("full mode needs a judge: set EVAL_JUDGE_MODEL (see .env.example).")
    return cases, config
