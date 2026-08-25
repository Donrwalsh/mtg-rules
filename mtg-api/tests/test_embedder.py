from mtg_api.embedder import Embedder


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
