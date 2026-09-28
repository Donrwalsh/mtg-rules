import json

import yaml

from mtg_evals.paths import default_eval_file, default_parsed_dir
from mtg_evals.validate import validate


def _jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _parsed(tmp_path):
    parsed = tmp_path / "parsed"
    parsed.mkdir()
    # An older rules file that would make 999.9z valid, to prove the latest wins.
    _jsonl(parsed / "rules_2020-01-01.jsonl", [{"rule_id": "999.9z", "text": "old"}])
    _jsonl(
        parsed / "rules_2026-08-25.jsonl",
        [{"rule_id": "702.11", "text": "Hexproof"}, {"rule_id": "702.11b", "text": "..."}],
    )
    _jsonl(
        parsed / "cards_2026-09-26.jsonl",
        [
            {"oracle_id": "o-bolt", "name": "Lightning Bolt"},
            {"oracle_id": "o-lightning", "name": "Lightning"},
            {"oracle_id": "o-bear", "name": "Grizzly Bears"},
        ],
    )
    _jsonl(parsed / "rulings_2026-08-25.jsonl", [{"oracle_id": "o-bolt", "comment": "..."}])
    return parsed


def _case(**fields):
    return {"question": "q?", "tags": ["keyword"], "split": "dev", **fields}


def _eval(tmp_path, entries):
    path = tmp_path / "eval.yaml"
    path.write_text(yaml.safe_dump(entries), encoding="utf-8")
    return path


def test_clean_file_has_no_problems(tmp_path):
    entries = [
        _case(
            id="ok",
            required_sources={"rules": ["702.11"], "rulings": ["Lightning Bolt"]},
            expected_cards=["Lightning Bolt"],
            forbidden_cards=["Lightning"],
        )
    ]
    assert validate(_eval(tmp_path, entries), _parsed(tmp_path)) == []


def test_reports_bad_rule_missing_card_duplicate_id_and_card_without_rulings(tmp_path):
    entries = [
        _case(id="bad-rule", required_sources={"rules": ["702.11b", "999.9z"]}),
        _case(id="missing-card", expected_cards=["Lightning Blot"]),
        _case(id="missing-card", question="again?"),
        _case(id="no-rulings", required_sources={"rulings": ["Grizzly Bears"]}),
    ]
    problems = validate(_eval(tmp_path, entries), _parsed(tmp_path))
    assert problems == [
        "'missing-card': duplicate id",
        "'bad-rule': rule '999.9z' matches no rule ID",
        "'missing-card': expected_cards card 'Lightning Blot' does not exist",
        "'no-rulings': 'Grizzly Bears' has no rulings",
    ]


def test_rule_prefix_matches_subrules(tmp_path):
    entries = [_case(id="prefix", required_sources={"rules": ["702.1"]})]
    assert validate(_eval(tmp_path, entries), _parsed(tmp_path)) == []


def test_real_eval_file_against_real_data_runs():
    # Not asserting clean: that's the live `validate` command's job. This
    # guards against crashes on the real file and data.
    assert isinstance(validate(default_eval_file(), default_parsed_dir()), list)
