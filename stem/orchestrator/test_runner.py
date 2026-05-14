"""Loading and invoking the developer's test functions (domain/tests.py)."""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class TestResult:
    passed: bool
    details: str = ""


def load_tests_module(domain_dir: Path):
    """Load domains/<name>/tests.py as a Python module.

    Returns None if no tests.py exists — in that case tasks are unverifiable
    and the orchestrator will record attempts as 'submitted, not verified'.
    """
    path = domain_dir / "tests.py"
    if not path.exists():
        return None
    module_name = f"_domain_tests_{domain_dir.name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def get_test_function(module, name: str) -> Callable:
    if not hasattr(module, name):
        raise AttributeError(f"tests module has no function '{name}'")
    return getattr(module, name)


def run_test(
    *,
    tests_module,
    task: dict,
    attempt: dict,
    environment_context: dict | None = None,
) -> TestResult:
    """Run the test function defined for this task."""
    if tests_module is None:
        return TestResult(False, "no tests.py in domain; task is unverifiable")
    spec = task.get("verification") or {}
    fn_name = spec.get("function")
    if not fn_name:
        return TestResult(False, "task has no verification.function defined")
    try:
        fn = get_test_function(tests_module, fn_name)
    except AttributeError as exc:
        return TestResult(False, str(exc))
    try:
        out = fn(task=task, attempt=attempt, environment_context=environment_context or {})
    except Exception as exc:
        return TestResult(False, f"test function raised: {type(exc).__name__}: {exc}")
    if isinstance(out, TestResult):
        return out
    if isinstance(out, bool):
        return TestResult(passed=out, details="")
    if isinstance(out, tuple) and len(out) == 2:
        return TestResult(passed=bool(out[0]), details=str(out[1]))
    return TestResult(False, f"test function returned unexpected: {type(out).__name__}: {out!r}")
