import asyncio

from mtg_api.main import app, lifespan


def test_lifespan_warms_all_caches(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr("mtg_api.main.get_rules_index", lambda: calls.append("rules_index"))
    monkeypatch.setattr("mtg_api.main.get_card_matcher", lambda: calls.append("card_matcher"))
    monkeypatch.setattr("mtg_api.main.get_keyword_matcher", lambda: calls.append("keyword_matcher"))
    monkeypatch.setattr("mtg_api.main.get_dense_embedder", lambda: calls.append("dense_embedder"))
    monkeypatch.setattr("mtg_api.main.get_sparse_embedder", lambda: calls.append("sparse_embedder"))
    monkeypatch.setattr("mtg_api.main.get_answerer", lambda: calls.append("answerer"))
    monkeypatch.setattr("mtg_api.main.get_data_version", lambda: calls.append("data_version"))

    async def _run():
        async with lifespan(app):
            pass

    asyncio.run(_run())

    assert set(calls) == {
        "rules_index",
        "card_matcher",
        "keyword_matcher",
        "dense_embedder",
        "sparse_embedder",
        "answerer",
        "data_version",
    }


def test_embedders_load_through_fastembed_with_configured_threads(monkeypatch):
    from mtg_api import main

    calls: list[tuple] = []
    monkeypatch.setattr(main.settings, "embed_threads", 2)
    monkeypatch.setattr(
        main,
        "load_fastembed_embedder",
        lambda name, threads: calls.append(("dense", name, threads)),
    )
    monkeypatch.setattr(
        main,
        "load_bm25_sparse_embedder",
        lambda name, threads: calls.append(("sparse", name, threads)),
    )
    main.get_dense_embedder.cache_clear()
    main.get_sparse_embedder.cache_clear()
    try:
        main.get_dense_embedder()
        main.get_sparse_embedder()
    finally:
        main.get_dense_embedder.cache_clear()
        main.get_sparse_embedder.cache_clear()
    assert calls == [
        ("dense", main.settings.dense_model_name, 2),
        ("sparse", main.settings.sparse_model_name, 2),
    ]


def test_lifespan_refuses_gating_without_prices(monkeypatch):
    import pytest

    from mtg_api import main

    monkeypatch.setattr(main.settings, "gating_enabled", True)
    monkeypatch.setattr(main.settings, "gemini_input_price_per_mtok", 0.0)

    async def _run():
        async with lifespan(app):
            pass

    with pytest.raises(RuntimeError, match="PRICE"):
        asyncio.run(_run())
