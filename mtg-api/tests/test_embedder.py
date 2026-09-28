import numpy as np

from mtg_api.embedder import Embedder, FastEmbedModel, load_fastembed_embedder


class FakeModel:
    def __init__(self, dim: int = 4):
        self._dim = dim
        self.calls: list[tuple[int, int]] = []

    def encode(self, texts, batch_size, show_progress_bar=False):
        self.calls.append((len(texts), batch_size))
        return [[float(len(t))] * self._dim for t in texts]

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim


def test_vector_size_comes_from_the_model():
    embedder = Embedder(FakeModel(dim=768), batch_size=1)
    assert embedder.vector_size == 768


def test_encode_returns_one_vector_per_text():
    embedder = Embedder(FakeModel(dim=4), batch_size=1)
    vectors = embedder.encode(["a", "bb"])
    assert len(vectors) == 2
    assert len(vectors[0]) == 4


def test_encode_empty_list_returns_empty_list_without_calling_the_model():
    model = FakeModel()
    embedder = Embedder(model, batch_size=1)
    assert embedder.encode([]) == []
    assert model.calls == []


class FakeFastEmbedModel:
    """fastembed.TextEmbedding's shape: embed() yields numpy arrays."""

    def __init__(self, dim: int = 3):
        self.embedding_size = dim
        self.calls: list[tuple[list[str], int]] = []

    def embed(self, texts, batch_size=256):
        self.calls.append((list(texts), batch_size))
        return (np.full(self.embedding_size, len(t), dtype=np.float32) for t in texts)


def test_fastembed_model_returns_plain_float_lists():
    vectors = FastEmbedModel(FakeFastEmbedModel(dim=3)).encode(
        ["ab", "c"], batch_size=1, show_progress_bar=False
    )
    assert vectors == [[2.0, 2.0, 2.0], [1.0, 1.0, 1.0]]
    assert type(vectors[0][0]) is float


def test_fastembed_model_passes_batch_size_through():
    model = FakeFastEmbedModel()
    FastEmbedModel(model).encode(["a"], batch_size=7, show_progress_bar=False)
    assert model.calls == [(["a"], 7)]


def test_fastembed_model_dimension_comes_from_embedding_size():
    embedder = Embedder(FastEmbedModel(FakeFastEmbedModel(dim=768)))
    assert embedder.vector_size == 768


def test_load_fastembed_embedder_passes_model_name_and_threads(monkeypatch):
    import fastembed

    created: list[dict] = []

    def fake_text_embedding(**kwargs):
        created.append(kwargs)
        return FakeFastEmbedModel()

    monkeypatch.setattr(fastembed, "TextEmbedding", fake_text_embedding)
    embedder = load_fastembed_embedder("BAAI/bge-base-en-v1.5", threads=2)
    assert created == [{"model_name": "BAAI/bge-base-en-v1.5", "threads": 2}]
    assert embedder.encode(["a"]) == [[1.0, 1.0, 1.0]]
