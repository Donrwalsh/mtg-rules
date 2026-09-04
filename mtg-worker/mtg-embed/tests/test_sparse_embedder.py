from mtg_embed.sparse_embedder import SparseEmbedder, SparseVector


class _FakeSparseEmbedding:
    def __init__(self, indices, values):
        self.indices = indices
        self.values = values


class FakeSparseModel:
    """Stands in for fastembed.SparseTextEmbedding's interface."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [
            _FakeSparseEmbedding(indices=list(range(len(t))), values=[1.0] * len(t))
            for t in texts
        ]


def test_encode_returns_one_sparse_vector_per_text():
    embedder = SparseEmbedder(FakeSparseModel())
    vectors = embedder.encode(["ab", "c"])
    assert len(vectors) == 2
    assert isinstance(vectors[0], SparseVector)
    assert vectors[0].indices == [0, 1]
    assert vectors[0].values == [1.0, 1.0]
    assert vectors[1].indices == [0]


def test_encode_empty_list_returns_empty_list_without_calling_the_model():
    model = FakeSparseModel()
    embedder = SparseEmbedder(model)
    assert embedder.encode([]) == []
    assert model.calls == []


def test_encode_passes_texts_through_to_the_model():
    model = FakeSparseModel()
    embedder = SparseEmbedder(model)
    embedder.encode(["x", "yy"])
    assert model.calls == [["x", "yy"]]
