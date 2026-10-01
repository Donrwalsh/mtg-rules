import pytest

from mtg_evals.paths import baseline_path


@pytest.mark.parametrize(
    ("mode", "generator", "name"),
    [
        ("retrieval", "ollama:phi4:latest", "baseline-retrieval.json"),
        ("full", None, "baseline-full.json"),
        ("full", "gemini:gemini-3.5-flash", "baseline-full.json"),
        ("full", "gemini:gemini-3.5-flash:think=low", "baseline-full.json"),
        ("full", "ollama:phi4:latest", "baseline-full-phi4.json"),
        ("full", "ollama:phi4:14b-q8_0", "baseline-full-phi4-14b-q8-0.json"),
    ],
)
def test_baseline_path_per_generator(mode, generator, name):
    assert baseline_path(mode, generator).name == name
