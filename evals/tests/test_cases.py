import pytest
import yaml

from mtg_evals.cases import CaseFileError, load_cases, schema_errors, select
from mtg_evals.paths import default_eval_file

VALID = {
    "id": "hexproof-own-target",
    "question": "Can I target my own hexproof creature?",
    "tags": ["keyword", "hexproof"],
    "split": "dev",
    "required_sources": {"rules": ["702.11b"], "rulings": ["Lightning Bolt"]},
    "expected_cards": ["Lightning Bolt"],
    "forbidden_cards": ["Lightning"],
    "gold_answer": "Yes.",
    "verdict": "yes",
}


def _write(tmp_path, entries):
    path = tmp_path / "eval.yaml"
    path.write_text(yaml.safe_dump(entries), encoding="utf-8")
    return path


def test_valid_case_loads(tmp_path):
    [case] = load_cases(_write(tmp_path, [VALID]))
    assert case.id == "hexproof-own-target"
    assert case.category == "keyword"
    assert case.rules == ("702.11b",)
    assert case.requirements == [("rules", "702.11b"), ("rulings", "Lightning Bolt")]
    assert case.has_required
    assert case.forbidden_cards == ("Lightning",)
    assert case.should_decline is False


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"id": None}, "missing id"),
        ({"split": "train"}, "split must be one of"),
        ({"tags": ["hexproof"]}, "is not a category"),
        ({"tags": []}, "tags must be a non-empty list"),
        ({"colour": "blue"}, "unknown key 'colour'"),
        ({"required_sources": {"rule": ["1"]}}, "unknown required_sources key 'rule'"),
        ({"required_sources": {"rules": "702.11b"}}, "required_sources.rules must be a list"),
        ({"verdict": "maybe"}, "verdict must be yes or no"),
        ({"should_decline": "yes"}, "should_decline must be true or false"),
        ({"expected_cards": "Lightning Bolt"}, "expected_cards must be a list"),
    ],
)
def test_schema_errors_name_the_case_and_problem(change, message):
    entry = {k: v for k, v in {**VALID, **change}.items() if v is not None}
    errors = schema_errors([entry])
    assert any(message in e for e in errors), errors


def test_duplicate_ids_are_reported():
    errors = schema_errors([VALID, dict(VALID)])
    assert errors == ["'hexproof-own-target': duplicate id"]


def test_load_cases_raises_with_every_error(tmp_path):
    bad = [{**VALID, "split": "x"}, {**VALID, "id": "b", "verdict": "maybe"}]
    with pytest.raises(CaseFileError) as info:
        load_cases(_write(tmp_path, bad))
    assert len(info.value.errors) == 2


def test_negative_case_has_no_requirements(tmp_path):
    entry = {
        "id": "neg",
        "question": "Who designed Magic?",
        "tags": ["negative"],
        "split": "dev",
        "should_decline": True,
    }
    [case] = load_cases(_write(tmp_path, [entry]))
    assert not case.has_required


def test_select_filters_by_split_tag_and_id(tmp_path):
    entries = [
        {**VALID, "id": "a", "split": "dev", "tags": ["keyword", "combat"]},
        {**VALID, "id": "b", "split": "test", "tags": ["keyword"]},
        {**VALID, "id": "c", "split": "dev", "tags": ["messy", "combat"]},
    ]
    cases = load_cases(_write(tmp_path, entries))
    assert [c.id for c in select(cases, "dev")] == ["a", "c"]
    assert [c.id for c in select(cases, "all")] == ["a", "b", "c"]
    assert [c.id for c in select(cases, "all", tags=["combat"])] == ["a", "c"]
    assert [c.id for c in select(cases, "dev", tags=["messy", "keyword"])] == ["a", "c"]
    assert [c.id for c in select(cases, "all", ids=["b"])] == ["b"]
    assert select(cases, "test", ids=["a"]) == []


def test_the_real_eval_file_is_schema_valid():
    cases = load_cases(default_eval_file())
    assert len(cases) == 50


def test_unquoted_yaml_yes_no_verdicts_load_as_strings(tmp_path):
    path = tmp_path / "eval.yaml"
    path.write_text(
        "- id: a\n  question: q?\n  tags: [keyword]\n  split: dev\n  verdict: no\n",
        encoding="utf-8",
    )
    [case] = load_cases(path)
    assert case.verdict == "no"
