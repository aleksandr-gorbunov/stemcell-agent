"""Stop criterion engine.

In the new lifecycle, successful exit is driven by the agent itself (via
declare_ready_for_inference). The orchestrator's role here is to enforce safety
nets: architectural-failure detection and budget caps. The agent cannot see
thresholds.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StopConfig:
    pass_rate_failure_floor: float = 0.30
    failure_window: int = 10
    min_iterations_before_failure_check: int = 10
    budget_max_iterations: int = 50
    budget_max_tokens: int = 500_000
    budget_max_wall_seconds: int = 1800


@dataclass
class StopState:
    iterations_run: int = 0
    pass_rate_history: list[float] = field(default_factory=list)
    tokens_used: int = 0
    wall_seconds_elapsed: float = 0.0


class StopDecision:
    CONTINUE = "continue"
    ARCH_FAILURE = "architectural_failure"
    BUDGET = "budget_exceeded"


@dataclass
class Decision:
    kind: str
    reason: str = ""


def evaluate(
    state: StopState,
    cfg: StopConfig,
    *,
    pass_rate: float,
    all_tasks_attempted: bool,
) -> Decision:
    """Decide whether the orchestrator should keep running the agent.

    Note: success exit is NOT decided here — that comes from the agent calling
    declare_ready_for_inference, which the runner handles directly.
    """
    if state.iterations_run >= cfg.budget_max_iterations:
        return Decision(StopDecision.BUDGET, f"hit max iterations ({cfg.budget_max_iterations})")
    if state.tokens_used >= cfg.budget_max_tokens:
        return Decision(StopDecision.BUDGET, f"hit max tokens ({cfg.budget_max_tokens})")
    if state.wall_seconds_elapsed >= cfg.budget_max_wall_seconds:
        return Decision(StopDecision.BUDGET, f"hit max wall time ({cfg.budget_max_wall_seconds}s)")

    if state.iterations_run < cfg.min_iterations_before_failure_check:
        return Decision(StopDecision.CONTINUE)

    if pass_rate < cfg.pass_rate_failure_floor and all_tasks_attempted:
        return Decision(
            StopDecision.ARCH_FAILURE,
            f"pass_rate={pass_rate:.2f} below floor={cfg.pass_rate_failure_floor:.2f} after {state.iterations_run} iters",
        )

    history = state.pass_rate_history
    if len(history) >= cfg.failure_window:
        window = history[-cfg.failure_window:]
        if max(window) - min(window) <= 0.02 and pass_rate < 0.5:
            return Decision(
                StopDecision.ARCH_FAILURE,
                f"no measurable improvement over last {cfg.failure_window} iters at pass_rate={pass_rate:.2f}",
            )

    return Decision(StopDecision.CONTINUE)
