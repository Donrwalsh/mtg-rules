from mtg_api.card_details import card_details

NORMAL = "https://cards.scryfall.io/normal/front/a/4/abc.jpg?1783907750"


def test_card_details_from_a_parsed_row():
    details = card_details(
        {
            "oracle_id": "oid",
            "name": "Colossal Dreadmaw",
            "type_line": "Creature — Dinosaur",
            "mana_cost": "{4}{G}{G}",
            "power": "6",
            "toughness": "6",
            "image_uri": NORMAL,
        }
    )
    assert details.name == "Colossal Dreadmaw"
    assert details.mana_cost == "{4}{G}{G}"
    assert (details.power, details.toughness, details.loyalty) == ("6", "6", None)
    assert details.image_normal == NORMAL
    assert details.image_small == "https://cards.scryfall.io/small/front/a/4/abc.jpg?1783907750"
    assert details.image_large == "https://cards.scryfall.io/large/front/a/4/abc.jpg?1783907750"


def test_card_details_tolerates_rows_from_before_the_new_fields():
    details = card_details({"oracle_id": "oid", "name": "Counterspell"})
    assert details.type_line == ""
    assert details.image_small is None and details.image_normal is None
    assert details.image_large is None
