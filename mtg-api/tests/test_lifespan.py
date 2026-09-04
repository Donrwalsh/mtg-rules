import asyncio

from mtg_api.main import app, lifespan


def test_lifespan_warms_all_three_caches(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr("mtg_api.main.get_card_matcher", lambda: calls.append("card_matcher"))
    monkeypatch.setattr("mtg_api.main.get_dense_embedder", lambda: calls.append("dense_embedder"))
    monkeypatch.setattr("mtg_api.main.get_sparse_embedder", lambda: calls.append("sparse_embedder"))

    async def _run():
        async with lifespan(app):
            pass

    asyncio.run(_run())

    assert set(calls) == {"card_matcher", "dense_embedder", "sparse_embedder"}
