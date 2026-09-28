import json

import httpx
import pytest

from mtg_evals.cache import JsonCache, answer_key, judge_key
from mtg_evals.cases import Case
from mtg_evals.judge import Judge, JudgeConfig, build_messages, is_judged, parse_grade

ANSWER_ARGS = ("Q?", "ctxhash", "ollama:phi4", 1, {"generation_temperature": 0})
JUDGE_ARGS = ("Q?", "Gold.", "Answer.", "llama3", 1)


def test_answer_key_is_stable_and_order_independent():
    first = answer_key(*ANSWER_ARGS)
    assert first == answer_key(*ANSWER_ARGS)
    a = answer_key("Q?", "h", "g", 1, {"ollama_model": "x", "generation_temperature": 0})
    b = answer_key("Q?", "h", "g", 1, {"generation_temperature": 0, "ollama_model": "x"})
    assert a == b


@pytest.mark.parametrize("index", range(5))
def test_answer_key_changes_with_each_component(index):
    changed = list(ANSWER_ARGS)
    changed[index] = {"generation_temperature": 1} if index == 4 else f"{changed[index]}x"
    assert answer_key(*changed) != answer_key(*ANSWER_ARGS)


@pytest.mark.parametrize("index", range(5))
def test_judge_key_changes_with_each_component(index):
    changed = list(JUDGE_ARGS)
    changed[index] = f"{changed[index]}x"
    assert judge_key(*changed) != judge_key(*JUDGE_ARGS)
    assert judge_key(*JUDGE_ARGS) == judge_key(*JUDGE_ARGS)


def test_answer_and_judge_keys_never_collide():
    assert answer_key("a", "b", "c", 1, {}) != judge_key("a", "b", "c", "1", 1)


def test_json_cache_roundtrip(tmp_path):
    cache = JsonCache(tmp_path, "answers")
    assert cache.get("k") is None
    cache.put("k", {"answer": "Yes."})
    assert cache.get("k") == {"answer": "Yes."}
    assert (tmp_path / "answers" / "k.json").exists()


def _case(**fields):
    return Case(id="c", question="Can I?", tags=("keyword",), split="dev", **fields)


def test_parse_grade_accepts_bare_and_fenced_json():
    assert parse_grade('{"grade": "correct", "reason": "Same."}', False) == {
        "grade": "correct",
        "reason": "Same.",
    }
    fenced = 'Sure!\n```json\n{"grade": "Partial", "reason": "Misses X."}\n```'
    assert parse_grade(fenced, False)["grade"] == "partial"


def test_parse_grade_rejects_grades_from_the_wrong_scale():
    assert parse_grade('{"grade": "correct", "reason": ""}', True)["grade"] == "error"
    assert parse_grade('{"grade": "declined_properly"}', True)["grade"] == "declined_properly"
    assert parse_grade("not json", False)["grade"] == "error"
    assert parse_grade(None, False)["grade"] == "error"


def test_decline_cases_use_the_decline_prompt():
    decline = build_messages(_case(should_decline=True), "I can't say.")[1]["content"]
    assert "declined_properly" in decline
    normal = build_messages(_case(gold_answer="Yes."), "Yes.")[1]["content"]
    assert "Reference answer: Yes." in normal


def test_is_judged():
    assert is_judged(_case(gold_answer="Yes."))
    assert is_judged(_case(should_decline=True))
    assert not is_judged(_case())


def test_judge_posts_temperature_zero_to_chat_completions():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        content = '{"grade": "incorrect", "reason": "Opposite conclusion."}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    judge = Judge(JudgeConfig("http://judge/v1/", "llama3", "key"), http)

    result = judge.grade(_case(gold_answer="Yes."), "No.")

    assert result == {"grade": "incorrect", "reason": "Opposite conclusion."}
    assert seen["url"] == "http://judge/v1/chat/completions"
    assert seen["auth"] == "Bearer key"
    assert seen["body"]["temperature"] == 0
    assert seen["body"]["model"] == "llama3"


def test_judge_config_from_env(monkeypatch):
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "qwen2.5")
    monkeypatch.delenv("EVAL_JUDGE_BASE_URL", raising=False)
    config = JudgeConfig.from_env()
    assert config.model == "qwen2.5"
    assert config.base_url == "http://localhost:11434/v1"
