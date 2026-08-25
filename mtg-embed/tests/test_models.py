from mtg_embed.models import EmbeddableChunk


def test_embeddable_chunk_holds_its_fields():
    chunk = EmbeddableChunk(
        point_id="some-id",
        source_type="rule",
        text_to_embed="Section 100: General\nSome rule text.",
        content_hash="hash-1",
        payload={"source_type": "rule", "rule_id": "100.1"},
    )
    assert chunk.point_id == "some-id"
    assert chunk.source_type == "rule"
    assert chunk.payload["rule_id"] == "100.1"
