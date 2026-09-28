from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

import httpx

from mtg_evals.cases import Case

# Bump whenever the judge prompts change: judge verdicts are cached on it.
JUDGE_PROMPT_VERSION = 1

ANSWER_GRADES = ("correct", "partial", "incorrect")
DECLINE_GRADES = ("declined_properly", "answered_anyway")

_SYSTEM = (
    "You grade answers from a Magic: The Gathering rules assistant. You are strict, "
    "consistent and brief. Reply with a single JSON object and nothing else."
)

_ANSWER_TEMPLATE = """Grade the assistant's answer against the reference answer.

Question: {question}

Reference answer: {gold}

Assistant's answer:
<<<
{answer}
>>>

Grades:
- "correct": reaches the same conclusion as the reference and says nothing that contradicts it. \
Extra correct detail and citation markers like [1] are fine.
- "partial": the right direction but missing a key point from the reference, or hedged or \
muddled enough that a player could be misled.
- "incorrect": a different conclusion, a rules error, or no real answer.

Reply exactly as: {{"grade": "correct" | "partial" | "incorrect", "reason": "<one sentence>"}}"""

_DECLINE_TEMPLATE = """This question is outside what the assistant can answer from its sources \
(the Comprehensive Rules, official rulings and card text). The assistant should say it can't \
answer from its context rather than give an answer.

Question: {question}

Assistant's answer:
<<<
{answer}
>>>

Grades:
- "declined_properly": it says the context doesn't cover this and does not present an answer \
as fact. Briefly pointing elsewhere is fine.
- "answered_anyway": it gives an answer (even hedged) as though it knew.

Reply exactly as: {{"grade": "declined_properly" | "answered_anyway", "reason": "<one sentence>"}}"""


@dataclass(frozen=True)
class JudgeConfig:
    base_url: str
    model: str
    api_key: str

    @classmethod
    def from_env(cls) -> JudgeConfig:
        return cls(
            base_url=os.environ.get("EVAL_JUDGE_BASE_URL") or "http://localhost:11434/v1",
            model=os.environ.get("EVAL_JUDGE_MODEL") or "",
            api_key=os.environ.get("EVAL_JUDGE_API_KEY") or "ollama",
        )


def is_judged(case: Case) -> bool:
    return case.should_decline or bool(case.gold_answer)


def build_messages(case: Case, answer: str) -> list[dict]:
    if case.should_decline:
        prompt = _DECLINE_TEMPLATE.format(question=case.question, answer=answer)
    else:
        prompt = _ANSWER_TEMPLATE.format(
            question=case.question, gold=case.gold_answer, answer=answer
        )
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}]


_JSON_OBJECT = re.compile(r"\{.*?\}", re.DOTALL)


def parse_grade(text: str | None, decline: bool) -> dict:
    """{"grade", "reason"}; grade "error" when the reply is unusable."""
    allowed = DECLINE_GRADES if decline else ANSWER_GRADES
    for match in _JSON_OBJECT.finditer(text or ""):
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        grade = str(data.get("grade", "")).strip().lower()
        if grade in allowed:
            return {"grade": grade, "reason": str(data.get("reason", "")).strip()}
    return {"grade": "error", "reason": f"unparseable judge reply: {(text or '')[:200]!r}"}


class Judge:
    def __init__(self, config: JudgeConfig, http: httpx.Client | None = None):
        self.config = config
        self._http = http or httpx.Client(timeout=300.0)

    def grade(self, case: Case, answer: str) -> dict:
        response = self._http.post(
            f"{self.config.base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {self.config.api_key}"},
            json={
                "model": self.config.model,
                "temperature": 0,
                "messages": build_messages(case, answer),
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return parse_grade(content, case.should_decline)
