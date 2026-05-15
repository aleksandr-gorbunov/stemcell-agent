"""Top-level orchestrator: drives the agent through phases, persists state, handles save/reject."""
from __future__ import annotations

import json
import os
import random
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents import Agent, Runner

from stem.agent.base_tools import IterationState, Phase, PathPolicy, build_base_tools
from stem.orchestrator.phases import (
    DomainDefinition,
    render_baseline_context,
    render_evaluation_context,
    render_inference_context,
    render_initialization_context,
    render_self_modification_context,
    render_self_testing_context,
)
from stem.orchestrator.checkpoint import create_checkpoint
from stem.orchestrator.status_tracker import StatusTracker
from stem.orchestrator.stop_criterion import StopConfig, StopDecision, StopState, evaluate
from stem.orchestrator.test_runner import load_verifier_module, run_test
from stem.orchestrator.workspace import (
    WorkspaceLayout,
    chmod_immutable,
    diff_mtimes,
    summarize_workspace,
    workspace_mtimes,
)


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AGENT_CORE = REPO_ROOT / "stem" / "agent"
RESULTS_DIR = REPO_ROOT / "results"


def _write_results_log(mode: str, summary: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    path = RESULTS_DIR / f"{mode}_{ts}.json"
    path.write_text(json.dumps(summary, indent=2, default=str))
    return path


_USAGE_FIELDS = ("input_tokens", "output_tokens", "total_tokens", "requests")


def _extract_usage(run_result) -> dict:
    """Sum token usage across all model responses in a RunResult. Zero if result is an error dict."""
    out = dict.fromkeys(_USAGE_FIELDS, 0)
    for r in getattr(run_result, "raw_responses", []) or []:
        u = getattr(r, "usage", None)
        if u is None:
            continue
        for k in _USAGE_FIELDS:
            out[k] += getattr(u, k, 0) or 0
    return out


def _add_usage(a: dict, b: dict) -> dict:
    return {k: a.get(k, 0) + b.get(k, 0) for k in _USAGE_FIELDS}


# Per-tool whitelist of params to surface in the trace. Keeps the rendered trace
# compact and avoids leaking huge tool arguments (e.g. full http body queries)
# back into the next iteration's context.
_TRACE_ARG_WHITELIST: dict[str, tuple[str, ...]] = {
    "read_file": ("path",),
    "write_file": ("path",),
    "list_files": ("directory",),
    "shell_exec": ("command",),
    "http_request": ("method", "url"),
    "list_skills": (),
    "submit_attempt": ("task_id",),
    "halt_with_explanation": (),
    "enter_self_testing": (),
    "enter_self_modification": (),
    "declare_ready_for_inference": (),
    "web_search": ("query",),
}


def _extract_tool_trace(run_result) -> tuple[dict[str, int], list[dict]]:
    """Pull tool-call counts and the ordered trace from a RunResult's new_items.

    The SDK already records each tool call as a ToolCallItem in `new_items`;
    we just shape it into the (counts, trace) the orchestrator needs.
    """
    counts: dict[str, int] = {}
    trace: list[dict] = []
    for item in getattr(run_result, "new_items", None) or []:
        if getattr(item, "type", None) != "tool_call_item":
            continue
        raw = getattr(item, "raw_item", None)
        if raw is None:
            continue
        name = getattr(raw, "name", None) or "?"
        args_raw = getattr(raw, "arguments", None)
        try:
            parsed = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
        except (json.JSONDecodeError, TypeError):
            parsed = {}
        whitelist = _TRACE_ARG_WHITELIST.get(name)
        if whitelist is None:
            shown = parsed if isinstance(parsed, dict) else {}
        else:
            shown = {k: parsed[k] for k in whitelist if isinstance(parsed, dict) and k in parsed}
        # Truncate any long string values (shell commands etc.) to keep the
        # trace lightweight in the next-iteration context.
        shown = {k: (v[:200] if isinstance(v, str) else v) for k, v in shown.items()}
        counts[name] = counts.get(name, 0) + 1
        trace.append({"name": name, "args": shown})
    return counts, trace


@dataclass
class RunConfig:
    domain_dir: Path
    workspace_dir: Path
    checkpoints_dir: Path
    evals_dir: Path | None = None
    model: str = "gpt-5.5"
    max_iterations: int = 50
    max_steps_per_iteration: int = 25
    cleanup_workspace_at_start: bool = True


# ---------------------------------------------------------------------------
# State persistence: survives across CLI invocations so the READY pause works.
# ---------------------------------------------------------------------------

@dataclass
class OrchestratorState:
    phase: str = Phase.INITIALIZATION.value
    iteration: int = 0
    domain_path: str | None = None
    pass_rate_history: list[float] = field(default_factory=list)
    halt_reason: str | None = None
    forcing_message: str | None = None
    run_id: str | None = None   # ISO-ish UTC timestamp of the training start, e.g. "2026-05-15T01-23-45Z"
    last_attempt: dict | None = None   # Most recent submit_attempt + result, surfaced to next iteration's context

    @classmethod
    def load(cls, path: Path) -> "OrchestratorState":
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        return cls(**data)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.__dict__, indent=2, default=str))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_bootstrap_prompt() -> str:
    return (AGENT_CORE / "BOOTSTRAP_PROMPT.md").read_text()


def _sanitized_task_view(tracker) -> list[dict]:
    """Tracker's per-task view with verifier-side details (last_failure) stripped.

    The orchestrator keeps last_failure_details in task_status.json for our own
    inspection, but never surfaces them to the agent: leaking verifier feedback
    across iterations would partially encode the solution into the training signal.
    """
    out = []
    for v in tracker.per_task_view():
        out.append({k: val for k, val in v.items() if k != "last_failure"})
    return out


def _ensure_clean_workspace(workspace_dir: Path) -> None:
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)
    layout = WorkspaceLayout(workspace_dir)
    layout.ensure()


def _make_verify_fn(tests_module, task_by_id: dict, domain_dir: Path):
    """Wrap the verifier as a `(task_id, attempt_dict) -> (passed, details)` callback for submit_attempt."""
    if tests_module is None:
        return None

    def verify(task_id: str, attempt: dict) -> tuple[bool, str]:
        task = task_by_id.get(task_id)
        if task is None:
            return False, f"unknown task_id: {task_id}"
        res = run_test(
            tests_module=tests_module,
            task=task,
            attempt=attempt,
            environment_context={"domain_dir": str(domain_dir)},
        )
        return res.passed, res.details

    return verify


async def _build_agent(
    *,
    instructions: str,
    layout: WorkspaceLayout,
    domain: DomainDefinition,
    iter_state: IterationState,
    model: str,
    phase: Phase,
    verify_fn=None,
) -> Agent:
    # Expose the workspace root so authored Python scripts can locate
    # environment.yaml and other workspace files without depending on __file__
    # path depth.
    os.environ["STEMCELL_AGENT_WORKSPACE"] = str(layout.root.resolve())
    policy = PathPolicy(workspace=layout.root, domain_dir=domain.domain_dir, phase=phase)
    tools = build_base_tools(policy, iter_state, verify_fn=verify_fn)
    return Agent(name="stem", instructions=instructions, model=model, tools=tools)


async def _run_one_iteration(
    *,
    agent: Agent,
    user_message: str,
    iter_state: IterationState,
    max_steps: int,
) -> Any:
    iter_state.reset()
    try:
        return await Runner.run(agent, user_message, max_turns=max_steps)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Main training driver
# ---------------------------------------------------------------------------

async def run_training(cfg: RunConfig, stop_cfg: StopConfig | None = None) -> dict:
    """Drive the agent through INITIALIZATION → SELF_MOD ⇄ SELF_TEST → READY.

    If the persisted state is READY, this raises: the caller should use
    `stem evaluate`, `stem save`, or `stem reject` instead.
    """
    stop_cfg = stop_cfg or StopConfig(budget_max_iterations=cfg.max_iterations)

    if cfg.cleanup_workspace_at_start:
        _ensure_clean_workspace(cfg.workspace_dir)
    layout = WorkspaceLayout(cfg.workspace_dir)
    layout.ensure()
    chmod_immutable(AGENT_CORE)

    domain = DomainDefinition.load(cfg.domain_dir)
    tests_module = load_verifier_module(evals_dir=cfg.evals_dir, domain_dir=domain.domain_dir)
    task_by_id = {t["id"]: t for t in domain.tasks}
    verify_fn = _make_verify_fn(tests_module, task_by_id, domain.domain_dir)
    tracker = StatusTracker(layout.status_file)
    tracker.initialize(domain.tasks, preserve_existing=not cfg.cleanup_workspace_at_start)

    state = OrchestratorState.load(layout.state_file)
    if cfg.cleanup_workspace_at_start:
        state = OrchestratorState(domain_path=str(cfg.domain_dir))
    if not state.run_id:
        state.run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    state.save(layout.state_file)

    # All checkpoint + summary writes for this training invocation go under
    # checkpoints/<domain>/<run_id>/ so successive runs don't overwrite each other.
    run_checkpoints_dir = cfg.checkpoints_dir / state.run_id

    instructions = _read_bootstrap_prompt()
    iter_state = IterationState()

    log_lines: list[str] = []
    iteration_records: list[dict] = []
    started_at = time.time()
    total_usage = dict.fromkeys(_USAGE_FIELDS, 0)
    stop_state = StopState(pass_rate_history=list(state.pass_rate_history))

    current_phase = Phase(state.phase)

    # If we are resuming a previous run and phase is READY, halt early.
    if current_phase == Phase.READY:
        return {
            "status": "ready",
            "note": "agent already in READY; use `stem evaluate`, `stem save --name <n>`, or `stem reject`",
        }

    while True:
        stop_state.iterations_run = state.iteration
        stop_state.wall_seconds_elapsed = time.time() - started_at

        # Phase-specific rendering and execution.
        agent = await _build_agent(
            instructions=instructions,
            layout=layout,
            domain=domain,
            iter_state=iter_state,
            model=cfg.model,
            phase=current_phase,
            verify_fn=verify_fn,
        )

        focused_task: dict | None = None
        if current_phase == Phase.INITIALIZATION:
            msg = render_initialization_context(domain)
        elif current_phase == Phase.SELF_MODIFICATION:
            msg = render_self_modification_context(
                iteration=state.iteration,
                workspace_summary=summarize_workspace(layout),
                task_view=_sanitized_task_view(tracker),
                forcing_message=state.forcing_message,
                last_attempt=state.last_attempt,
            )
            state.forcing_message = None
        elif current_phase == Phase.SELF_TESTING:
            focused_task = random.choice(domain.tasks) if domain.tasks else {}
            other_statuses = [
                v for v in _sanitized_task_view(tracker)
                if v.get("task_id") != focused_task.get("id")
            ]
            msg = render_self_testing_context(
                iteration=state.iteration,
                workspace_summary=summarize_workspace(layout),
                focused_task=focused_task,
                other_task_statuses=other_statuses,
                forcing_message=state.forcing_message,
                last_attempt=state.last_attempt,
            )
            state.forcing_message = None
        else:
            raise RuntimeError(f"unexpected phase in training loop: {current_phase}")

        mtimes_before = workspace_mtimes(layout)
        iter_started_at = time.time()
        log_lines.append(
            f"[{datetime.now(timezone.utc).isoformat()}] {current_phase.value} iter={state.iteration}"
        )
        run_result = await _run_one_iteration(
            agent=agent,
            user_message=msg,
            iter_state=iter_state,
            max_steps=cfg.max_steps_per_iteration,
        )
        iter_usage = _extract_usage(run_result)
        total_usage = _add_usage(total_usage, iter_usage)
        tool_counts, tool_trace = _extract_tool_trace(run_result)
        workspace_changes = diff_mtimes(mtimes_before, workspace_mtimes(layout))

        # Halt requested?
        if iter_state.halt_reason:
            log_lines.append(f"  agent requested halt: {iter_state.halt_reason}")
            state.halt_reason = iter_state.halt_reason
            state.save(layout.state_file)
            break

        # Process attempts. Verification happened synchronously inside submit_attempt,
        # so each attempt already carries `passed` and `details`. We just propagate to
        # the tracker and our structured record.
        verified_attempts: list[dict] = []
        last_passed: bool | None = None
        for attempt in iter_state.attempts:
            tid = attempt["task_id"]
            passed = bool(attempt.get("passed"))
            details = attempt.get("details", "")
            tracker.record_attempt(tid, passed, attempt.get("summary", ""), details)
            last_passed = passed
            verified_attempts.append({"task_id": tid, "passed": passed, "details": details})
            log_lines.append(
                f"  attempted {tid}: {'PASS' if passed else 'FAIL'} -- {details}"
            )

        # Capture the most recent submitted attempt so the next iteration's context
        # surfaces it back to the agent (without verifier-side hints), plus the
        # tool-call path that led to it.
        if iter_state.attempts:
            last = iter_state.attempts[-1]
            state.last_attempt = {
                "iteration": state.iteration,
                "task_id": last["task_id"],
                "answer": last.get("answer"),
                "passed": bool(last.get("passed")),
                "tool_call_log": tool_trace,
            }

        # Failure-log discipline: if the agent failed in this iteration and made
        # no workspace changes, force a retry with a directive.
        if verified_attempts and last_passed is False and not workspace_changes:
            state.forcing_message = (
                "Last iteration you attempted a task and it failed, but you made no changes to "
                "your workspace. The orchestrator requires that failures result in either a "
                "failure_log entry or a skill/tool change. Address the cause now before another "
                "attempt. (You may need to enter_self_modification first if you are still in "
                "SELF_TESTING.)"
            )

        agg = tracker.aggregate()
        state.pass_rate_history.append(agg["pass_rate"])
        stop_state.pass_rate_history = list(state.pass_rate_history)

        # Phase transition requested by the agent.
        next_phase = iter_state.requested_phase or current_phase
        if next_phase != current_phase:
            log_lines.append(f"  phase transition: {current_phase.value} -> {next_phase.value}")

        iteration_records.append({
            "iteration": state.iteration,
            "phase": current_phase.value,
            "focused_task": focused_task.get("id") if focused_task else None,
            "wall_seconds": time.time() - iter_started_at,
            "tool_calls": tool_counts,
            "workspace_changes": workspace_changes,
            "attempts": verified_attempts,
            "usage": iter_usage,
            "phase_transition": (
                f"{current_phase.value} -> {next_phase.value}" if next_phase != current_phase else None
            ),
        })

        # Initialization always transitions to SELF_MODIFICATION after its single iteration.
        if current_phase == Phase.INITIALIZATION and next_phase == Phase.INITIALIZATION:
            next_phase = Phase.SELF_MODIFICATION

        state.iteration += 1
        state.phase = next_phase.value
        state.save(layout.state_file)

        create_checkpoint(
            layout.root,
            run_checkpoints_dir,
            iteration=state.iteration,
            metadata={
                "phase_during_iter": current_phase.value,
                "next_phase": next_phase.value,
                "tracker": agg,
                "attempts": iter_state.attempts,
                "forcing_message_for_next": state.forcing_message,
            },
        )

        # READY pause → return to caller; user takes over.
        if next_phase == Phase.READY:
            log_lines.append("  agent entered READY; pausing for user review")
            return {
                "status": "ready",
                "iteration": state.iteration,
                "tracker": agg,
                "note": "Run `stem evaluate` to see eval performance, `stem save --name <n>` to commit, or `stem reject [--reason ...]` to send back.",
            }

        # Otherwise, check architectural failure / budget.
        decision = evaluate(
            stop_state,
            stop_cfg,
            pass_rate=agg["pass_rate"],
            all_tasks_attempted=tracker.all_attempted(),
        )
        if decision.kind != StopDecision.CONTINUE:
            log_lines.append(f"  stop_check: {decision.kind} -- {decision.reason}")
            state.halt_reason = f"{decision.kind}: {decision.reason}"
            state.save(layout.state_file)
            create_checkpoint(
                layout.root,
                run_checkpoints_dir,
                iteration=state.iteration,
                metadata={
                    "phase": "STOP",
                    "decision": decision.kind,
                    "reason": decision.reason,
                    "tracker": agg,
                },
            )
            break

        current_phase = next_phase

    summary = {
        "mode": "train",
        "model": cfg.model,
        "domain": domain.name,
        "run_id": state.run_id,
        "checkpoints_dir": str(run_checkpoints_dir),
        "started_at_utc": datetime.fromtimestamp(started_at, tz=timezone.utc).isoformat(timespec="seconds"),
        "ended_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "wall_seconds": time.time() - started_at,
        "ending_phase": state.phase,
        "iterations": state.iteration,
        "task_aggregate": tracker.aggregate(),
        "total_usage": total_usage,
        "halt_reason": state.halt_reason,
        "iteration_records": iteration_records,
        "log_lines": log_lines,
    }
    run_checkpoints_dir.mkdir(parents=True, exist_ok=True)
    (run_checkpoints_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    (run_checkpoints_dir / "log.txt").write_text("\n".join(log_lines))
    summary["results_path"] = str(_write_results_log("train", summary))
    return summary


# ---------------------------------------------------------------------------
# Evaluate a READY (or saved/loaded) workspace against the domain tasks
# ---------------------------------------------------------------------------

async def run_evaluation(
    *,
    workspace_dir: Path,
    domain_dir: Path,
    evals_dir: Path | None = None,
    model: str = "gpt-5.5",
    max_steps_per_iteration: int = 25,
    phase: Phase = Phase.INFERENCE,
) -> dict:
    """Evaluate a frozen workspace against the domain's eval examples.

    Pass phase=Phase.BASELINE when the workspace represents the untrained
    baseline (e.g. trained_agents/vanilla); the path policy then refuses
    domain-dir reads so the baseline cannot consult DESCRIPTION.md.
    """
    layout = WorkspaceLayout(workspace_dir)
    examples_path = (evals_dir / "examples.yaml") if evals_dir is not None else None
    domain = DomainDefinition.load(domain_dir, examples_path=examples_path)
    tests_module = load_verifier_module(evals_dir=evals_dir, domain_dir=domain.domain_dir)
    task_by_id = {t["id"]: t for t in domain.tasks}
    verify_fn = _make_verify_fn(tests_module, task_by_id, domain.domain_dir)
    instructions = _read_bootstrap_prompt()
    iter_state = IterationState()

    started_at = time.time()
    eval_results: list[dict] = []
    log_lines: list[str] = []
    total_usage = dict.fromkeys(_USAGE_FIELDS, 0)

    for task in domain.tasks:
        agent = await _build_agent(
            instructions=instructions,
            layout=layout,
            domain=domain,
            iter_state=iter_state,
            model=model,
            phase=phase,
            verify_fn=verify_fn,
        )
        if phase == Phase.BASELINE:
            msg = render_baseline_context(task)
        else:
            msg = render_evaluation_context(summarize_workspace(layout), task)
        log_lines.append(f"[{datetime.now(timezone.utc).isoformat()}] {phase.value} task={task['id']}")
        run_result = await _run_one_iteration(
            agent=agent,
            user_message=msg,
            iter_state=iter_state,
            max_steps=max_steps_per_iteration,
        )
        task_usage = _extract_usage(run_result)
        total_usage = _add_usage(total_usage, task_usage)
        task_counts, _task_trace = _extract_tool_trace(run_result)
        outcome: dict[str, Any] = {
            "task_id": task["id"],
            "attempts": list(iter_state.attempts),
            "tool_calls": task_counts,
            "usage": task_usage,
        }
        # submit_attempt verified inline; pick the agent's most recent attempt for this task.
        matching = [a for a in iter_state.attempts if a["task_id"] == task["id"]]
        if matching:
            last = matching[-1]
            outcome["passed"] = bool(last.get("passed"))
            outcome["details"] = last.get("details", "")
        else:
            outcome["passed"] = False
            outcome["details"] = "agent did not submit an attempt for this task"
        eval_results.append(outcome)
        log_lines.append(f"  -> {'PASS' if outcome['passed'] else 'FAIL'} -- {outcome['details']}")

    summary = {
        "mode": "evaluate",
        "model": model,
        "domain": domain.name,
        "workspace": str(workspace_dir),
        "started_at_utc": datetime.fromtimestamp(started_at, tz=timezone.utc).isoformat(timespec="seconds"),
        "ended_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "wall_seconds": time.time() - started_at,
        "eval_pass_rate": (
            sum(1 for r in eval_results if r.get("passed")) / len(eval_results)
            if eval_results else None
        ),
        "total_usage": total_usage,
        "eval_results": eval_results,
        "log_lines": log_lines,
    }
    summary["results_path"] = str(_write_results_log("evaluate", summary))
    return summary


# ---------------------------------------------------------------------------
# Inference (ad-hoc, user-driven; no examples, no verification)
# ---------------------------------------------------------------------------

async def run_inference(
    *,
    workspace_dir: Path,
    domain_dir: Path,
    instruction: str,
    model: str = "gpt-5.5",
    max_steps: int = 25,
) -> dict:
    """Run a trained agent on a single user-supplied instruction."""
    layout = WorkspaceLayout(workspace_dir)
    domain = DomainDefinition.load(domain_dir)
    iter_state = IterationState()
    agent = await _build_agent(
        instructions=_read_bootstrap_prompt(),
        layout=layout,
        domain=domain,
        iter_state=iter_state,
        model=model,
        phase=Phase.INFERENCE,
    )
    # submit_attempt is a task-bound primitive; remove it for free-form inference.
    agent.tools = [t for t in agent.tools if t.name != "submit_attempt"]

    msg = render_inference_context(summarize_workspace(layout), instruction)
    started_at = time.time()
    result = await _run_one_iteration(agent=agent, user_message=msg,
                                      iter_state=iter_state, max_steps=max_steps)
    final = getattr(result, "final_output", None) or str(result)

    summary = {
        "mode": "inference",
        "model": model,
        "workspace": str(workspace_dir),
        "domain": domain.name,
        "instruction": instruction,
        "response": final,
        "started_at_utc": datetime.fromtimestamp(started_at, tz=timezone.utc).isoformat(timespec="seconds"),
        "ended_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "wall_seconds": time.time() - started_at,
        "usage": _extract_usage(result),
    }
    summary["results_path"] = str(_write_results_log("inference", summary))
    return summary


# ---------------------------------------------------------------------------
# Save / Reject
# ---------------------------------------------------------------------------

def save_agent(
    *,
    workspace_dir: Path,
    domain_dir: Path,
    trained_agents_dir: Path,
    name: str,
    extra_metadata: dict | None = None,
) -> Path:
    """Copy a READY workspace to trained_agents/<name>/ with metadata."""
    target = trained_agents_dir / name
    if target.exists():
        raise FileExistsError(f"trained agent already exists: {target}")
    target.mkdir(parents=True)
    shutil.copytree(
        workspace_dir,
        target / "agent_workspace",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".orchestrator_state.json"),
    )
    metadata = {
        "name": name,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "domain_path": str(domain_dir),
        "domain_name": domain_dir.name,
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    (target / "metadata.json").write_text(json.dumps(metadata, indent=2))
    return target


def reject_agent(workspace_dir: Path, reason: str) -> None:
    """Append a rejection note to the failure log and return phase to SELF_MODIFICATION."""
    layout = WorkspaceLayout(workspace_dir)
    state = OrchestratorState.load(layout.state_file)
    state.phase = Phase.SELF_MODIFICATION.value
    state.forcing_message = (
        "The user rejected your declared-ready state with the following note. Address it before "
        "declaring ready again.\n\nReason: " + reason
    )
    state.save(layout.state_file)
    # Also drop a note into the failure log so it's visible inline.
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with layout.failure_log.open("a") as fh:
        fh.write(f"\n## Rejected by user @ {ts}\n\n{reason}\n")
