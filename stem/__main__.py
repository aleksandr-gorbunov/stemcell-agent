"""Command-line entry point."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from stem.orchestrator.runner import (
    RunConfig,
    reject_agent,
    run_evaluation,
    run_naive_baseline,
    run_training,
    save_agent,
)
from stem.orchestrator.stop_criterion import StopConfig


REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Argument resolution
# ---------------------------------------------------------------------------

def _resolve_domain_dir(value: str) -> Path:
    p = Path(value).resolve()
    if not p.exists() or not p.is_dir():
        raise SystemExit(f"domain directory not found: {p}")
    if not (p / "DESCRIPTION.md").exists():
        raise SystemExit(f"domain {p} is missing DESCRIPTION.md")
    if not (p / "tasks.yaml").exists():
        raise SystemExit(f"domain {p} is missing tasks.yaml")
    return p


def _default_workspace() -> Path:
    return (REPO_ROOT / "agent_workspace").resolve()


def _default_checkpoints(domain_dir: Path) -> Path:
    return (REPO_ROOT / "checkpoints" / domain_dir.name).resolve()


def _default_trained_agents() -> Path:
    return (REPO_ROOT / "trained_agents").resolve()


def _build_runconfig(args, *, domain_dir: Path) -> RunConfig:
    workspace = Path(args.workspace).resolve() if args.workspace else _default_workspace()
    checkpoints = Path(args.checkpoints).resolve() if args.checkpoints else _default_checkpoints(domain_dir)
    return RunConfig(
        domain_dir=domain_dir,
        workspace_dir=workspace,
        checkpoints_dir=checkpoints,
        model=args.model or os.environ.get("STEMCELL_MODEL", "gpt-5.5"),
        max_iterations=args.max_iterations,
        cleanup_workspace_at_start=not getattr(args, "resume", False),
    )


def add_common_args(p: argparse.ArgumentParser, *, require_domain: bool = True) -> None:
    p.add_argument(
        "--domain",
        dest="domain_dir",
        required=require_domain,
        help="Path to domains/<name>/ directory",
    )
    p.add_argument("--workspace", help="Override workspace path (default: ./agent_workspace)")
    p.add_argument("--checkpoints", help="Override checkpoints path (default: ./checkpoints/<domain>)")
    p.add_argument("--model", help="Override main agent model (STEMCELL_MODEL)")
    p.add_argument("--max-iterations", type=int, default=50)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_train(args) -> int:
    domain_dir = _resolve_domain_dir(args.domain_dir)
    runcfg = _build_runconfig(args, domain_dir=domain_dir)
    stopcfg = StopConfig(budget_max_iterations=args.max_iterations)
    summary = asyncio.run(run_training(runcfg, stopcfg))
    print(json.dumps(summary, indent=2, default=str))
    return 0


def cmd_evaluate(args) -> int:
    """Evaluate either the current agent_workspace/ (READY) or a saved trained agent."""
    if args.load:
        trained_dir = Path(args.load).resolve()
        if not trained_dir.exists():
            raise SystemExit(f"trained agent not found: {trained_dir}")
        ws = trained_dir / "agent_workspace"
        metadata_path = trained_dir / "metadata.json"
        if metadata_path.exists():
            meta = json.loads(metadata_path.read_text())
            domain_path = args.domain_dir or meta.get("domain_path")
        else:
            domain_path = args.domain_dir
        if not domain_path:
            raise SystemExit(
                "could not determine domain to evaluate against; provide --domain or ensure metadata.json has domain_path"
            )
        domain_dir = _resolve_domain_dir(domain_path)
    else:
        ws = _default_workspace() if not args.workspace else Path(args.workspace).resolve()
        if not ws.exists():
            raise SystemExit(f"agent workspace not found: {ws}")
        domain_dir = _resolve_domain_dir(args.domain_dir)

    summary = asyncio.run(run_evaluation(
        workspace_dir=ws,
        domain_dir=domain_dir,
        model=args.model or os.environ.get("STEMCELL_MODEL", "gpt-5.5"),
    ))
    print(json.dumps(summary, indent=2, default=str))
    return 0


def cmd_save(args) -> int:
    ws = _default_workspace() if not args.workspace else Path(args.workspace).resolve()
    if not ws.exists():
        raise SystemExit(f"agent workspace not found: {ws}")
    domain_dir = _resolve_domain_dir(args.domain_dir)
    trained_dir = Path(args.trained_agents_dir).resolve() if args.trained_agents_dir else _default_trained_agents()
    target = save_agent(
        workspace_dir=ws,
        domain_dir=domain_dir,
        trained_agents_dir=trained_dir,
        name=args.name,
    )
    print(f"saved to: {target}")
    return 0


def cmd_reject(args) -> int:
    ws = _default_workspace() if not args.workspace else Path(args.workspace).resolve()
    if not ws.exists():
        raise SystemExit(f"agent workspace not found: {ws}")
    reject_agent(workspace_dir=ws, reason=args.reason or "(no reason given)")
    print("rejection recorded; phase reset to SELF_MODIFICATION. Run `stem train --domain ... --resume` to continue.")
    return 0


def cmd_baseline(args) -> int:
    domain_dir = _resolve_domain_dir(args.domain_dir)
    runcfg = _build_runconfig(args, domain_dir=domain_dir)
    summary = asyncio.run(run_naive_baseline(runcfg))
    print(json.dumps(summary, indent=2, default=str))
    return 0


def cmd_inference(args) -> int:
    """Alias for `stem evaluate --load ...`."""
    return cmd_evaluate(args)


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="stem", description="Stem agent orchestrator")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_train = sub.add_parser("train", help="Train the stem against a domain")
    add_common_args(p_train)
    p_train.add_argument("--resume", action="store_true", help="Continue from existing workspace state instead of clearing")
    p_train.set_defaults(func=cmd_train)

    p_eval = sub.add_parser("evaluate", help="Evaluate the current workspace or a saved trained agent")
    add_common_args(p_eval, require_domain=False)
    p_eval.add_argument("--load", help="Path to trained_agents/<name>/ to evaluate")
    p_eval.set_defaults(func=cmd_evaluate)

    p_save = sub.add_parser("save", help="Save the READY workspace to trained_agents/<name>/")
    add_common_args(p_save)
    p_save.add_argument("--name", required=True, help="Name under which to save the trained agent")
    p_save.add_argument("--trained-agents-dir", help="Override default trained_agents path")
    p_save.set_defaults(func=cmd_save)

    p_reject = sub.add_parser("reject", help="Reject the READY workspace and send the agent back to SELF_MODIFICATION")
    add_common_args(p_reject, require_domain=False)
    p_reject.add_argument("--reason", help="Reason to record (will be visible to the agent in its failure log)")
    p_reject.set_defaults(func=cmd_reject)

    p_base = sub.add_parser("baseline", help="Run naive baseline (empty workspace, base tools only)")
    add_common_args(p_base)
    p_base.set_defaults(func=cmd_baseline)

    p_inf = sub.add_parser("inference", help="Alias for `evaluate --load <trained_agents path>`")
    add_common_args(p_inf, require_domain=False)
    p_inf.add_argument("--load", required=True)
    p_inf.set_defaults(func=cmd_inference)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
