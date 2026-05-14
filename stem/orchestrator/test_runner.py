"""Load and invoke the developer's verifier.py."""
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


def load_verifier_module(*, evals_dir: Path | None, domain_dir: Path):
    """Import verifier.py from evals_dir if present, otherwise from domain_dir. None if neither."""
    candidates: list[Path] = []
    if evals_dir is not None:
        candidates.append(evals_dir / "verifier.py")
    candidates.append(domain_dir / "verifier.py")

    for path in candidates:
        if path.exists():
            module_name = f"_verifier_{path.parent.name}"
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise RuntimeError(f"could not import {path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            return module
    return None


def run_test(
    *,
    tests_module,
    task: dict,
    attempt: dict,
    environment_context: dict | None = None,
) -> TestResult:
    if tests_module is None:
        return TestResult(False, "no verifier.py found; task is unverifiable")
    fn_name = (task.get("verification") or {}).get("function")
    if not fn_name:
        return TestResult(False, "task has no verification.function defined")
    if not hasattr(tests_module, fn_name):
        return TestResult(False, f"verifier module has no function '{fn_name}'")
    fn: Callable = getattr(tests_module, fn_name)
    try:
        out = fn(task=task, attempt=attempt, environment_context=environment_context or {})
    except Exception as exc:
        return TestResult(False, f"verifier raised: {type(exc).__name__}: {exc}")
    if isinstance(out, TestResult):
        return out
    if isinstance(out, bool):
        return TestResult(passed=out, details="")
    if isinstance(out, tuple) and len(out) == 2:
        return TestResult(passed=bool(out[0]), details=str(out[1]))
    return TestResult(False, f"verifier returned unexpected: {type(out).__name__}: {out!r}")
