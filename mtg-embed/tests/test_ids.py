from mtg_embed.ids import oracle_point_id, ruling_point_id, rule_point_id


def test_rule_point_id_is_deterministic():
    assert rule_point_id("100.1a") == rule_point_id("100.1a")


def test_different_rule_ids_get_different_points():
    assert rule_point_id("100.1a") != rule_point_id("100.1b")


def test_rule_and_oracle_ids_never_collide_on_the_same_raw_key():
    assert rule_point_id("abc") != oracle_point_id("abc")


def test_ruling_point_id_is_deterministic_and_index_sensitive():
    assert ruling_point_id("oid-1", 0) == ruling_point_id("oid-1", 0)
    assert ruling_point_id("oid-1", 0) != ruling_point_id("oid-1", 1)
