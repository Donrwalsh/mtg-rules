def test_settings_defaults(monkeypatch):
    for key in (
        "MTG_EMBED_QDRANT_HOST",
        "MTG_EMBED_QDRANT_PORT",
        "MTG_EMBED_COLLECTION_NAME",
        "MTG_EMBED_MODEL_NAME",
    ):
        monkeypatch.delenv(key, raising=False)

    from mtg_embed.config import Settings

    s = Settings()
    assert s.qdrant_host == "localhost"
    assert s.qdrant_port == 6333
    assert s.collection_name == "mtg_rules"
    assert s.model_name == "BAAI/bge-base-en-v1.5"
    assert s.embed_batch_size == 32
    assert s.retrieve_batch_size == 256


def test_settings_env_override(monkeypatch):
    monkeypatch.setenv("MTG_EMBED_QDRANT_HOST", "qdrant")
    monkeypatch.setenv("MTG_EMBED_QDRANT_PORT", "7000")
    monkeypatch.setenv("MTG_EMBED_COLLECTION_NAME", "custom_collection")

    from mtg_embed.config import Settings

    s = Settings()
    assert s.qdrant_host == "qdrant"
    assert s.qdrant_port == 7000
    assert s.collection_name == "custom_collection"


def test_settings_sparse_model_default():
    from mtg_embed.config import Settings

    s = Settings()
    assert s.sparse_model_name == "Qdrant/bm25"
