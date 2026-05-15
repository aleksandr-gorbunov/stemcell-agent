"""Template verifier module for a domain with an eval split.

Each function named in `examples.yaml` under `verification.function` must be
exported here. The orchestrator imports this module and calls the named
function with keyword arguments (task, attempt, environment_context) and
expects either a (passed: bool, details: str) tuple or a TestResult.

Functions typically read the expected answer for the task from a sibling
`answers.yaml` keyed by `task["id"]`. Keep the cache pattern below if your
answers file is large; remove it if you would rather re-read on every call.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import yaml
from openai import OpenAI


_ANSWERS_PATH = Path(__file__).parent / "answers.yaml"
_ANSWERS_CACHE: dict | None = None


def _answers() -> dict:
    global _ANSWERS_CACHE
    if _ANSWERS_CACHE is None:
        _ANSWERS_CACHE = yaml.safe_load(_ANSWERS_PATH.read_text())
    return _ANSWERS_CACHE


def _lookup(task: dict) -> dict:
    tid = task.get("id")
    answers = _answers()
    if tid not in answers:
        raise KeyError(f"no answer entry for task id {tid!r}")
    return answers[tid]["expected"]


def verify_example(*, task, attempt, environment_context):
    """Deterministic verifier. Compares the agent's answer against the
    expected entry in answers.yaml. Use whenever exact-match (or a precise
    structural check you can code) is feasible.

    Add as many functions like this as your tasks need. The name referenced
    in examples.yaml's `verification.function` must match an exported
    function here.
    """
    expected = _lookup(task)
    actual = attempt.get("answer")
    if actual == expected.get("value"):
        return True, "ok"
    return False, f"expected {expected.get('value')!r}, got {actual!r}"


def verify_llm_judge(*, task, attempt, environment_context):
    """LLM-as-judge verifier. Asks an LLM to grade the agent's answer
    against the expected answer using the model named in
    `STEMCELL_VERIFIER_MODEL`. Use for free-text answers, summaries, or
    anything where coding an exact-match check would be brittle.

    The expected entry can be anything the judge prompt should consider: a
    reference answer, a set of required points, a rubric. Shape it however
    your prompt needs it.
    """
    expected = _lookup(task)
    actual = attempt.get("answer")
    model = os.environ.get("STEMCELL_VERIFIER_MODEL", "gpt-5-mini")

    prompt = (
        "Grade an agent's answer to a task. Return JSON only.\n\n"
        f"Task: {task.get('instruction', '')}\n\n"
        f"Reference / criteria: {json.dumps(expected, ensure_ascii=False)}\n\n"
        f"Agent's answer: {json.dumps(actual, ensure_ascii=False)}\n\n"
        "Return {\"passed\": true|false, \"reason\": \"<one-sentence reason>\"}."
    )

    schema = {
        "type": "object",
        "properties": {
            "passed": {"type": "boolean"},
            "reason": {"type": "string"},
        },
        "required": ["passed", "reason"],
        "additionalProperties": False,
    }
    resp = OpenAI().responses.create(
        model=model,
        input=prompt,
        text={"format": {"type": "json_schema", "name": "grade",
                         "strict": True, "schema": schema}},
    )
    out = json.loads(resp.output_text)
    return bool(out["passed"]), out.get("reason", "")
