"""Per-iteration workspace checkpoints.

Each call to `create_checkpoint` copies the agent's workspace into
`checkpoints/iter_NNNN/agent_workspace/` and writes a metadata.json alongside
it. Used to build an audit trail of how the workspace evolved during training.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


def create_checkpoint(workspace: Path, checkpoints_root: Path, iteration: int, metadata: dict) -> Path:
    checkpoints_root.mkdir(parents=True, exist_ok=True)
    iter_dir = checkpoints_root / f"iter_{iteration:04d}"
    if iter_dir.exists():
        shutil.rmtree(iter_dir)
    iter_dir.mkdir(parents=True)
    if workspace.exists():
        # We skip the orchestrator's own state file when copying, since the next
        # iteration writes a fresh one anyway.
        shutil.copytree(
            workspace,
            iter_dir / "agent_workspace",
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".orchestrator_state.json"),
        )
    meta = dict(metadata)
    meta.setdefault("timestamp", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    meta.setdefault("iteration", iteration)
    (iter_dir / "metadata.json").write_text(json.dumps(meta, indent=2, default=str))
    return iter_dir


def latest_iteration(checkpoints_root: Path) -> int:
    if not checkpoints_root.exists():
        return -1
    iters = sorted(checkpoints_root.glob("iter_*"))
    if not iters:
        return -1
    return int(iters[-1].name.split("_")[1])
