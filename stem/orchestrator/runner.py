"""Top-level orchestrator: drives the agent through phases, persists state, handles save/reject."""
from __future__ import annotations

import json
import os
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
# State persistence — survives across CLI invocations so the READY pause works.
# ---------------------------------------------------------------------------

@dataclass
class OrchestratorState:
    phase: str = Phase.INITIALIZATION.value
    iteration: int = 0
    domain_path: str | None = None
    pass_rate_history: list[float] = field(default_factory=list)
    halt_reason: str | None = None
    forcing_message: str | None = None

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


def _ensure_clean_workspace(workspace_dir: Path) -> None:
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)
    layout = WorkspaceLayout(workspace_dir)
    layout.ensure()


async def _build_agent(
    *,
    instructions: str,
    layout: WorkspaceLayout,
    domain: DomainDefinition,
    iter_state: IterationState,
    model: str,
    phase: Phase,
) -> Agent:
    # Expose the workspace root so authored Python scripts can locate
    # environment.yaml and other workspace files without depending on __file__
    # path depth.
    os.environ["STEMCELL_AGENT_WORKSPACE"] = str(layout.root.resolve())
    policy = PathPolicy(workspace=layout.root, domain_dir=domain.domain_dir, phase=phase)
    tools = build_base_tools(policy, iter_state)
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

    If the persisted state is READY, this raises — the caller should use
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
    tracker = StatusTracker(layout.status_file)
    tracker.initialize(domain.tasks, preserve_existing=not cfg.cleanup_workspace_at_start)

    state = OrchestratorState.load(layout.state_file)
    if cfg.cleanup_workspace_at_start:
        state = OrchestratorState(domain_path=str(cfg.domain_dir))
    state.save(layout.state_file)

    instructions = _read_bootstrap_prompt()
    iter_state = IterationState()

    log_lines: list[str] = []
    started_at = time.time()
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
        )

        if current_phase == Phase.INITIALIZATION:
            msg = render_initialization_context(domain)
        elif current_phase == Phase.SELF_MODIFICATION:
            msg = render_self_modification_context(
                iteration=state.iteration,
                workspace_summary=summarize_workspace(layout),
                task_view=tracker.per_task_view(),
                forcing_message=state.forcing_message,
            )
            state.forcing_message = None
        elif current_phase == Phase.SELF_TESTING:
            msg = render_self_testing_context(
                iteration=state.iteration,
                workspace_summary=summarize_workspace(layout),
                task_view=tracker.per_task_view(),
                forcing_message=state.forcing_message,
            )
            state.forcing_message = None
        else:
            raise RuntimeError(f"unexpected phase in training loop: {current_phase}")

        mtimes_before = workspace_mtimes(layout)
        log_lines.append(
            f"[{datetime.now(timezone.utc).isoformat()}] {current_phase.value} iter={state.iteration}"
        )
        await _run_one_iteration(
            agent=agent,
            user_message=msg,
            iter_state=iter_state,
            max_steps=cfg.max_steps_per_iteration,
        )

        # Halt requested?
        if iter_state.halt_reason:
            log_lines.append(f"  agent requested halt: {iter_state.halt_reason}")
            state.halt_reason = iter_state.halt_reason
            state.save(layout.state_file)
            break

        # Process attempts (only happens in SELF_TESTING).
        task_by_id = {t["id"]: t for t in domain.tasks}
        verified_any = False
        last_passed: bool | None = None
        for attempt in iter_state.attempts:
            tid = attempt["task_id"]
            if tid not in task_by_id:
                tracker.record_attempt(tid, False, attempt.get("summary", ""), "unknown task_id")
                continue
            res = run_test(
                tests_module=tests_module,
                task=task_by_id[tid],
                attempt=attempt,
                environment_context={"domain_dir": str(domain.domain_dir)},
            )
            tracker.record_attempt(tid, res.passed, attempt.get("summary", ""), res.details)
            verified_any = True
            last_passed = res.passed
            log_lines.append(
                f"  attempted {tid}: {'PASS' if res.passed else 'FAIL'} — {res.details}"
            )

        # Failure-log discipline: if the agent failed in this iteration and made
        # no workspace changes, force a retry with a directive.
        if verified_any and last_passed is False:
            mtimes_after = workspace_mtimes(layout)
            if not diff_mtimes(mtimes_before, mtimes_after):
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

        # Initialization always transitions to SELF_MODIFICATION after its single iteration.
        if current_phase == Phase.INITIALIZATION and next_phase == Phase.INITIALIZATION:
            next_phase = Phase.SELF_MODIFICATION

        state.iteration += 1
        state.phase = next_phase.value
        state.save(layout.state_file)

        create_checkpoint(
            layout.root,
            cfg.checkpoints_dir,
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
            log_lines.append(f"  stop_check: {decision.kind} — {decision.reason}")
            state.halt_reason = f"{decision.kind}: {decision.reason}"
            state.save(layout.state_file)
            create_checkpoint(
                layout.root,
                cfg.checkpoints_dir,
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

    # Write final summary.
    summary = {
        "ending_phase": state.phase,
        "domain": domain.name,
        "iterations": state.iteration,
        "wall_seconds": time.time() - started_at,
        "task_aggregate": tracker.aggregate(),
        "halt_reason": state.halt_reason,
    }
    cfg.checkpoints_dir.mkdir(parents=True, exist_ok=True)
    (cfg.checkpoints_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    (cfg.checkpoints_dir / "log.txt").write_text("\n".join(log_lines))
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
) -> dict:
    """INFERENCE-style eval over a frozen workspace (the READY workspace or a saved agent)."""
    layout = WorkspaceLayout(workspace_dir)
    examples_path = (evals_dir / "examples.yaml") if evals_dir is not None else None
    domain = DomainDefinition.load(domain_dir, examples_path=examples_path)
    tests_module = load_verifier_module(evals_dir=evals_dir, domain_dir=domain.domain_dir)
    instructions = _read_bootstrap_prompt()
    iter_state = IterationState()

    started_at = time.time()
    eval_results: list[dict] = []
    log_lines: list[str] = []

    for task in domain.tasks:
        agent = await _build_agent(
            instructions=instructions,
            layout=layout,
            domain=domain,
            iter_state=iter_state,
            model=model,
            phase=Phase.INFERENCE,
        )
        msg = render_inference_context(summarize_workspace(layout), task)
        log_lines.append(f"[{datetime.now(timezone.utc).isoformat()}] INFERENCE task={task['id']}")
        await _run_one_iteration(
            agent=agent,
            user_message=msg,
            iter_state=iter_state,
            max_steps=max_steps_per_iteration,
        )
        outcome: dict[str, Any] = {"task_id": task["id"], "attempts": iter_state.attempts}
        for att in iter_state.attempts:
            if att["task_id"] == task["id"]:
                res = run_test(
                    tests_module=tests_module,
                    task=task,
                    attempt=att,
                    environment_context={"domain_dir": str(domain.domain_dir)},
                )
                outcome["passed"] = res.passed
                outcome["details"] = res.details
                break
        else:
            outcome["passed"] = False
            outcome["details"] = "agent did not submit an attempt for this task"
        eval_results.append(outcome)
        log_lines.append(f"  -> {'PASS' if outcome['passed'] else 'FAIL'} — {outcome['details']}")

    summary = {
        "mode": "evaluation",
        "domain": domain.name,
        "workspace": str(workspace_dir),
        "wall_seconds": time.time() - started_at,
        "eval_results": eval_results,
        "eval_pass_rate": (
            sum(1 for r in eval_results if r.get("passed")) / len(eval_results)
            if eval_results else None
        ),
    }
    return summary


# ---------------------------------------------------------------------------
# Naive baseline
# ---------------------------------------------------------------------------

async def run_naive_baseline(cfg: RunConfig) -> dict:
    """Empty workspace, base tools only, run against the eval-mode examples."""
    workspace_dir = cfg.workspace_dir.parent / (cfg.workspace_dir.name + "_baseline")
    _ensure_clean_workspace(workspace_dir)
    layout = WorkspaceLayout(workspace_dir)
    layout.ensure()

    examples_path = (cfg.evals_dir / "examples.yaml") if cfg.evals_dir is not None else None
    domain = DomainDefinition.load(cfg.domain_dir, examples_path=examples_path)
    tests_module = load_verifier_module(evals_dir=cfg.evals_dir, domain_dir=domain.domain_dir)
    instructions = _read_bootstrap_prompt()
    iter_state = IterationState()

    log_lines: list[str] = []
    started_at = time.time()
    eval_results: list[dict] = []

    for task in domain.tasks:
        agent = await _build_agent(
            instructions=instructions,
            layout=layout,
            domain=domain,
            iter_state=iter_state,
            model=cfg.model,
            phase=Phase.BASELINE,
        )
        msg = render_baseline_context(task)
        log_lines.append(f"[{datetime.now(timezone.utc).isoformat()}] BASELINE task={task['id']}")
        await _run_one_iteration(
            agent=agent,
            user_message=msg,
            iter_state=iter_state,
            max_steps=cfg.max_steps_per_iteration,
        )
        outcome: dict[str, Any] = {"task_id": task["id"], "attempts": iter_state.attempts}
        for att in iter_state.attempts:
            if att["task_id"] == task["id"]:
                res = run_test(
                    tests_module=tests_module,
                    task=task,
                    attempt=att,
                    environment_context={"domain_dir": str(domain.domain_dir)},
                )
                outcome["passed"] = res.passed
                outcome["details"] = res.details
                break
        else:
            outcome["passed"] = False
            outcome["details"] = "agent did not submit an attempt for this task"
        eval_results.append(outcome)
        log_lines.append(f"  -> {'PASS' if outcome['passed'] else 'FAIL'} — {outcome['details']}")

    summary = {
        "mode": "naive_baseline",
        "domain": domain.name,
        "wall_seconds": time.time() - started_at,
        "eval_results": eval_results,
        "eval_pass_rate": (
            sum(1 for r in eval_results if r.get("passed")) / len(eval_results)
            if eval_results else None
        ),
    }
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
