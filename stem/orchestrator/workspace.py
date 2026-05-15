"""Workspace lifecycle and state-summary construction.

Fixed layout under agent_workspace/:

  skills/<skill_name>/
    SKILL.md             runbook for this skill
    tools/
      <name>.py          standalone Python scripts invoked via shell_exec

  KNOWLEDGE.md           narrative understanding of the domain (free, long)
  environment.yaml       structured operational data (URLs, env-var names, ids)
  FAILURE_LOG.md         what went wrong and why; editable; not append-only
  task_status.json       written by the orchestrator; per-task status

Filename convention: markdown documents are ALL_CAPS (README-style); Python
and YAML file basenames are lowercase. Directory names are lowercase. The
path conventions here are hardcoded; the agent and authored scripts should
rely on these exact names.
"""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# Canonical relative paths inside agent_workspace/. Treat as constants.
SKILLS_DIR = "skills"
KNOWLEDGE_FILE = "KNOWLEDGE.md"
ENVIRONMENT_FILE = "environment.yaml"
FAILURE_LOG_FILE = "FAILURE_LOG.md"
TASK_STATUS_FILE = "task_status.json"
ORCHESTRATOR_STATE_FILE = ".orchestrator_state.json"

# Skill-directory conventions.
SKILL_DOC_FILE = "SKILL.md"
SKILL_TOOLS_DIR = "tools"


@dataclass
class WorkspaceLayout:
    root: Path

    @property
    def skills(self) -> Path:
        return self.root / SKILLS_DIR

    @property
    def knowledge(self) -> Path:
        return self.root / KNOWLEDGE_FILE

    @property
    def environment(self) -> Path:
        return self.root / ENVIRONMENT_FILE

    @property
    def failure_log(self) -> Path:
        return self.root / FAILURE_LOG_FILE

    @property
    def status_file(self) -> Path:
        return self.root / TASK_STATUS_FILE

    @property
    def state_file(self) -> Path:
        """Orchestrator-owned: persists phase, iteration count, etc. between CLI invocations."""
        return self.root / ORCHESTRATOR_STATE_FILE

    def ensure(self) -> None:
        """Ensure the workspace skeleton exists.

        The environment.yaml is auto-created (empty) so that tools can read it
        unconditionally without having to defend against a missing file. The
        agent populates real values during SELF_MODIFICATION.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        self.skills.mkdir(exist_ok=True)
        if not self.environment.exists():
            self.environment.write_text("# Structured operational data the agent and its tools rely on.\n# Populated by the agent during SELF_MODIFICATION.\n")

    def iter_skill_dirs(self) -> Iterable[Path]:
        if not self.skills.exists():
            return []
        return sorted(p for p in self.skills.iterdir() if p.is_dir())

    def skill_doc(self, skill_dir: Path) -> Path:
        return skill_dir / SKILL_DOC_FILE

    def skill_tools_dir(self, skill_dir: Path) -> Path:
        return skill_dir / SKILL_TOOLS_DIR


def chmod_immutable(immutable_dir: Path) -> None:
    """Make a directory tree read+exec only, recursively. Best-effort."""
    if not immutable_dir.exists():
        return
    for path in [immutable_dir, *immutable_dir.rglob("*")]:
        try:
            if path.is_dir():
                os.chmod(path, stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
            else:
                os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        except PermissionError:
            pass


def summarize_workspace(layout: WorkspaceLayout) -> str:
    """Compact text summary suitable for injecting into the agent's user message.

    Lists skills with their description (first line of SKILL.md if present)
    plus tool counts. The agent uses this as a "table of contents" for the
    workspace; full SKILL.md is read on demand.
    """
    lines: list[str] = []
    skill_dirs = list(layout.iter_skill_dirs())
    if skill_dirs:
        lines.append(f"skills/ ({len(skill_dirs)} skills):")
        for sd in skill_dirs:
            doc = layout.skill_doc(sd)
            tools_dir = layout.skill_tools_dir(sd)
            py_count = sum(1 for _ in tools_dir.glob("*.py")) if tools_dir.exists() else 0
            first_line = ""
            if doc.exists():
                try:
                    text = doc.read_text()
                    for ln in text.splitlines():
                        stripped = ln.strip()
                        if stripped and not stripped.startswith("#"):
                            first_line = stripped[:120]
                            break
                except Exception:
                    pass
            tools_summary = f"{py_count} script{'s' if py_count != 1 else ''}" if py_count else "no scripts"
            lines.append(f"  - {sd.name}/ ({tools_summary}){': ' + first_line if first_line else ''}")
    else:
        lines.append("skills/ (no skills yet)")

    for f, label in [
        (layout.knowledge, KNOWLEDGE_FILE),
        (layout.environment, ENVIRONMENT_FILE),
        (layout.failure_log, FAILURE_LOG_FILE),
    ]:
        if f.exists():
            lines.append(f"{label} ({f.stat().st_size}b)")

    return "\n".join(lines) if lines else "(agent_workspace empty)"


def workspace_mtimes(layout: WorkspaceLayout) -> dict[str, float]:
    out = {}
    for p in layout.root.rglob("*"):
        if p.is_file():
            out[str(p.relative_to(layout.root))] = p.stat().st_mtime
    return out


def diff_mtimes(before: dict[str, float], after: dict[str, float]) -> list[str]:
    changed = []
    for k, v in after.items():
        if k not in before or before[k] != v:
            changed.append(k)
    for k in before:
        if k not in after:
            changed.append(f"deleted:{k}")
    return changed
