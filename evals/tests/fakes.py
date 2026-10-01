"""Fixture API/judge doubles for runner tests: no live stack needed."""

import hashlib

CONFIG = {
    "settings": {
        "hybrid_top_k": 10,
        "hybrid_dense_weight": 0.5,
        "gemini_model": "gemini-3.5-flash",
    },
    "generator": "gemini:gemini-3.5-flash",
    "prompt_version": 1,
    "collection": {"name": "mtg_rules", "points_count": 1000},
    "data_files": {"rules": "rules_2026-08-25.jsonl"},
}


class FakeConfigResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def rule_result(rule_id):
    return {
        "source": "rule",
        "source_type": "rule",
        "rule_id": rule_id,
        "card_name": None,
        "match_type": "vector_hit",
        "text": f"Rule {rule_id} text.",
        "score": 0.5,
    }


class FakeApi:
    """Returns canned results per question; records every query call."""

    base_url = "http://fake"

    def __init__(
        self,
        results_by_question=None,
        answer="Yes. Because [1].",
        config=None,
        health=None,
        config_status=200,
        generation_error=None,
    ):
        self.results_by_question = results_by_question or {}
        self.answer = answer
        self._config = config if config is not None else CONFIG
        self._health = health or {"status": "ok", "qdrant": "ok"}
        self._config_status = config_status
        self.generation_error = generation_error
        self.calls = []

    def health(self):
        return self._health

    def config(self):
        return FakeConfigResponse(self._config_status, self._config)

    def query(self, question, *, generate, overrides):
        self.calls.append({"question": question, "generate": generate, "overrides": overrides})
        results = self.results_by_question.get(question, [])
        context_hash = hashlib.sha256(repr(results).encode()).hexdigest()
        body = {
            "query": question,
            "results": results,
            "answer": self.answer if generate else None,
            "citations": [{"number": 1, **results[0]}] if generate and results else [],
            "citation_stats": {"cited_count": 1, "invalid_count": 0, "uncited_answer": False},
            "context_hash": context_hash,
            "prompt_version": 1,
            "generator": "gemini:" + overrides.get("gemini_model", "gemini-3.5-flash"),
            "generation_error": self.generation_error if generate else None,
            "usage": (
                {"input_tokens": 100, "output_tokens": 20, "thinking_tokens": 30}
                if generate
                else None
            ),
        }
        return body, 12.5


class FakeJudgeConfig:
    model = "judge-model"


class FakeJudge:
    config = FakeJudgeConfig()

    def __init__(self, grade="correct"):
        self._grade = grade
        self.calls = []

    def grade(self, case, answer):
        self.calls.append(case.id)
        grade = "declined_properly" if case.should_decline else self._grade
        return {"grade": grade, "reason": "Fake."}
