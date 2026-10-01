import hashlib
import json
from datetime import UTC, datetime

from conftest import memory_engine

from mtg_api.answer_cache import (
    DATA_VERSION_FILE,
    cache_key,
    get_cached,
    normalize_query,
    put_cached,
    read_data_version,
)
from mtg_api.config import OVERRIDABLE_SETTINGS, Settings
from mtg_api.llm import PROMPT_VERSION

NOW = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)


def test_normalize_query_folds_case_space_and_trailing_punctuation():
    assert normalize_query("  How does   TRAMPLE work?? ") == "how does trample work"
    assert normalize_query("Deathtouch + trample.") == "deathtouch + trample"


def test_cache_key_is_stable_across_normalization():
    s = Settings(_env_file=None)
    assert cache_key("Trample?", s, "v1") == cache_key("  trample ", s, "v1")


def test_cache_key_changes_with_data_version_and_settings():
    s = Settings(_env_file=None)
    base = cache_key("trample", s, "v1")
    assert cache_key("trample", s, "v2") != base
    assert cache_key("trample", s.model_copy(update={"hybrid_top_k": 3}), "v1") != base
    thinking = s.model_copy(update={"generation_thinking_level": "low"})
    assert cache_key("trample", thinking, "v1") != base


def test_put_then_get_round_trips_and_counts_hits():
    engine = memory_engine()
    put_cached(engine, "k", normalized_query="trample", response={"answer": "Yes [1]."}, now=NOW)
    assert get_cached(engine, "k") == ({"answer": "Yes [1]."}, NOW)
    get_cached(engine, "k")
    with engine.connect() as conn:
        from mtg_api.answer_cache import answer_cache

        assert conn.execute(answer_cache.select()).mappings().one()["hit_count"] == 2


def test_get_cached_misses():
    assert get_cached(memory_engine(), "nope") is None


def test_put_cached_replaces_an_existing_entry():
    engine = memory_engine()
    put_cached(engine, "k", normalized_query="q", response={"answer": "old"}, now=NOW)
    put_cached(engine, "k", normalized_query="q", response={"answer": "new"}, now=NOW)
    assert get_cached(engine, "k")[0] == {"answer": "new"}


def test_read_data_version_prefers_the_marker(tmp_path):
    (tmp_path / "rules_2026-08-25.jsonl").write_text("")
    (tmp_path / DATA_VERSION_FILE).write_text("2026-09-29T15:00:00+00:00\n")
    assert read_data_version(tmp_path) == "2026-09-29T15:00:00+00:00"


def test_read_data_version_falls_back_to_latest_file_names(tmp_path):
    for name in ("cards_2026-09-01.jsonl", "cards_2026-09-26.jsonl", "rules_2026-08-25.jsonl"):
        (tmp_path / name).write_text("")
    assert read_data_version(tmp_path) == "cards_2026-09-26.jsonl|rules_2026-08-25.jsonl"


def test_read_data_version_without_any_files(tmp_path):
    assert read_data_version(tmp_path / "missing") == "none|none"


def test_gemini_cache_keys_are_unchanged():
    s = Settings(_env_file=None)
    parts = {
        "query": normalize_query("Q"),
        "prompt_version": PROMPT_VERSION,
        "data_version": "v1",
        "settings": {name: getattr(s, name) for name in OVERRIDABLE_SETTINGS},
    }
    encoded = json.dumps(parts, sort_keys=True, default=str).encode("utf-8")
    assert cache_key("Q", s, "v1") == hashlib.sha256(encoded).hexdigest()


def test_cache_key_keeps_providers_apart():
    s = Settings(_env_file=None)
    ollama = s.model_copy(update={"answer_provider": "ollama"})
    assert cache_key("Q", s, "v1") != cache_key("Q", ollama, "v1")
    other_model = ollama.model_copy(update={"ollama_model": "llama3.1:8b"})
    assert cache_key("Q", ollama, "v1") != cache_key("Q", other_model, "v1")
