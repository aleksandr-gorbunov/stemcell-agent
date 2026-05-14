"""Deterministic verifiers for the security_analyst domain. Each one reads
expected values from answers.yaml in this directory."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


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
        raise KeyError(f"no answer entry for example id {tid!r}")
    return answers[tid]["expected"]


def _str(v: Any) -> str:
    return v if isinstance(v, str) else str(v)


def verify_incidents(*, task, attempt, environment_context):
    expected = _lookup(task)
    actual = attempt.get("answer")
    if not isinstance(actual, list):
        return False, f"answer must be a list of incident objects, got {type(actual).__name__}"

    missing = []
    for exp in expected.get("real_incidents", []):
        exp_type = exp["type"]
        exp_ind = exp["primary_indicator"].lower()
        if not any(
            isinstance(item, dict)
            and item.get("type") == exp_type
            and (exp_ind in _str(item.get("primary_indicator", "")).lower()
                 or _str(item.get("primary_indicator", "")).lower() in exp_ind)
            for item in actual
        ):
            missing.append(f"{exp_type}/{exp['primary_indicator']}")

    leaked = []
    for item in actual:
        if not isinstance(item, dict):
            continue
        haystack = " ".join(
            _str(item.get(k, "")) for k in ("primary_indicator", "evidence", "type")
        ).lower()
        for pat in expected.get("should_not_flag", []):
            for indicator in pat["indicator_substrings"]:
                if indicator.lower() in haystack:
                    leaked.append(f"{pat['pattern']} via {indicator!r}")
                    break

    parts = []
    if missing:
        parts.append("missing: " + ", ".join(missing))
    if leaked:
        parts.append("leaked: " + ", ".join(leaked))
    if parts:
        return False, "; ".join(parts)
    return True, f"matched {len(expected.get('real_incidents', []))} real incidents, no leaks"


def verify_enum(*, task, attempt, environment_context):
    expected_value = _lookup(task)["value"]
    actual = attempt.get("answer")
    # Accept either a bare string or a dict with one of a few conventional keys.
    if isinstance(actual, str):
        actual_value = actual
    elif isinstance(actual, dict):
        actual_value = next(
            (actual[k] for k in ("health_status", "value", "cause", "status") if k in actual),
            None,
        )
    else:
        actual_value = None

    if not isinstance(actual_value, str):
        return False, f"expected a string enum value, got {actual!r}"
    if actual_value.strip().lower() == expected_value.lower():
        return True, f"matched {expected_value!r}"
    return False, f"expected {expected_value!r}, got {actual_value!r}"


def verify_user_set(*, task, attempt, environment_context):
    expected = _lookup(task)
    actual = attempt.get("answer")
    if isinstance(actual, list):
        actual_users = actual
    elif isinstance(actual, dict) and isinstance(actual.get("users_to_investigate"), list):
        actual_users = actual["users_to_investigate"]
    else:
        return False, f"answer must be a list or {{users_to_investigate: [...]}}, got {actual!r}"

    expected_set = {_str(u).strip().lower() for u in expected["users_to_investigate"]}
    actual_set = {_str(u).strip().lower() for u in actual_users}
    missing, extra = expected_set - actual_set, actual_set - expected_set
    if not missing and not extra:
        return True, f"matched {sorted(expected_set)}"
    parts = []
    if missing:
        parts.append(f"missing: {sorted(missing)}")
    if extra:
        parts.append(f"extra: {sorted(extra)}")
    return False, "; ".join(parts)
