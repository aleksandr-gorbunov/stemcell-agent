"""Domain definition loader and per-phase user-message renderers."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml


# `DomainDefinition.tasks` holds the list of concrete examples (one per row of
# examples.yaml). The user-facing file name is `examples.yaml`; the Python
# attribute stays `tasks` to align with the agent's `submit_attempt(task_id=...)`
# API and the bootstrap prompt's vocabulary.


@dataclass
class DomainDefinition:
    name: str
    domain_dir: Path
    description_text: str
    capabilities: list[dict]
    reference_materials: list[Path]
    tasks: list[dict]

    @classmethod
    def load(cls, domain_dir: Path, examples_path: Path | None = None) -> "DomainDefinition":
        description_path = domain_dir / "DESCRIPTION.md"
        if not description_path.exists():
            raise FileNotFoundError(f"domain missing DESCRIPTION.md: {domain_dir}")
        description_text = description_path.read_text()

        materials_dir = domain_dir / "materials"
        reference_materials = sorted(materials_dir.glob("*")) if materials_dir.exists() else []

        if examples_path is None:
            examples_path = domain_dir / "examples.yaml"
        tasks = yaml.safe_load(examples_path.read_text()) if examples_path.exists() else []

        return cls(
            name=domain_dir.name,
            domain_dir=domain_dir,
            description_text=description_text,
            capabilities=_parse_capabilities_section(description_text),
            reference_materials=reference_materials,
            tasks=tasks or [],
        )


def _parse_capabilities_section(description: str) -> list[dict]:
    """Parse `- <id>: <description>` bullets under `## Capabilities`. Empty if absent."""
    in_section = False
    caps: list[dict] = []
    for ln in description.splitlines():
        if ln.strip().lower().startswith("## capabilities"):
            in_section = True
            continue
        if in_section and ln.startswith("## "):
            break
        if in_section:
            s = ln.strip()
            if s.startswith("- "):
                rest = s[2:].strip()
                if ":" in rest:
                    cid, desc = rest.split(":", 1)
                    caps.append({"id": cid.strip(), "description": desc.strip()})
                else:
                    caps.append({"id": rest, "description": ""})
    return caps


def render_initialization_context(domain: DomainDefinition) -> str:
    lines = [
        "# Phase: INITIALIZATION",
        "",
        "This is the very first iteration. Your workspace is empty.",
        "",
        "Read the domain description below and inspect the available materials. Then write an "
        "initial `KNOWLEDGE.md` capturing your first impression of the domain — what you think the "
        "domain is about, what entities are likely involved, what you will need to investigate.",
        "",
        "Do not yet attempt any tasks. The orchestrator will transition you to SELF_MODIFICATION "
        "automatically when this iteration ends.",
        "",
        "## Domain description",
        "",
        domain.description_text,
        "",
        f"## Reference materials available under {domain.domain_dir}/materials/ "
        f"({len(domain.reference_materials)} files)",
    ]
    for p in domain.reference_materials:
        lines.append(f"  - {p.name}")
    if not domain.reference_materials:
        lines.append("  (none provided)")
    return "\n".join(lines)


def render_self_modification_context(
    iteration: int,
    workspace_summary: str,
    task_view: list[dict],
    forcing_message: str | None = None,
) -> str:
    lines = [
        f"# Phase: SELF_MODIFICATION — iteration {iteration}",
        "",
        "Build and refine your specialization. Create skills under `skills/<name>/SKILL.md` and "
        "write Python scripts under `skills/<name>/tools/<name>.py`. Update `KNOWLEDGE.md` and "
        "`environment.yaml` as you discover the structure of the domain.",
        "",
        "You may not call `submit_attempt` in this phase. When ready to see how your current "
        "workspace performs on the task list, call `enter_self_testing()`.",
        "",
        "## Current workspace state",
        workspace_summary,
        "",
        "## Tasks and current statuses (visible for context, not attempted in this phase)",
        json.dumps(task_view, indent=2, ensure_ascii=False),
    ]
    if forcing_message:
        lines.extend(["", "## IMPORTANT FORCING MESSAGE", forcing_message])
    return "\n".join(lines)


def render_self_testing_context(
    iteration: int,
    workspace_summary: str,
    task_view: list[dict],
    forcing_message: str | None = None,
) -> str:
    lines = [
        f"# Phase: SELF_TESTING — iteration {iteration}",
        "",
        "Your workspace is frozen for this phase. You cannot write, append, or delete files, and "
        "you cannot create new skills or tools. Attempt tasks via `submit_attempt(task_id=..., "
        "summary=..., answer=...)`. Verification runs after this iteration ends.",
        "",
        "When testing has surfaced something you want to fix, call `enter_self_modification()`. "
        "When you believe your workspace is ready to be saved as a finalized trained agent, call "
        "`declare_ready_for_inference()`.",
        "",
        "## Current workspace state",
        workspace_summary,
        "",
        "## Tasks and current statuses",
        json.dumps(task_view, indent=2, ensure_ascii=False),
    ]
    if forcing_message:
        lines.extend(["", "## IMPORTANT FORCING MESSAGE", forcing_message])
    return "\n".join(lines)


def render_inference_context(
    workspace_summary: str,
    task: dict,
) -> str:
    return "\n".join([
        "# Phase: INFERENCE",
        "",
        "You are a previously trained agent loaded for use. Your workspace is frozen and read-only. "
        "You will be given one task at a time; solve it using the skills, tools, and knowledge you "
        "accumulated during training. You may not write new artifacts.",
        "",
        "## Current workspace state",
        workspace_summary,
        "",
        "## Task",
        json.dumps({k: v for k, v in task.items() if k != "verification"}, indent=2, ensure_ascii=False),
        "",
        "Call `submit_attempt(task_id=..., summary=..., answer=...)` when done.",
    ])


def render_baseline_context(task: dict) -> str:
    return "\n".join([
        "# Phase: BASELINE",
        "",
        "You have just been instantiated. Your workspace is empty and writes are disabled. You have "
        "base tools only — no skills, no authored tools, no knowledge file, no failure log. You will "
        "be given tasks one at a time. Solve each using base tools, then call "
        "`submit_attempt(task_id=..., summary=..., answer=...)`.",
        "",
        "## Task",
        json.dumps({k: v for k, v in task.items() if k != "verification"}, indent=2, ensure_ascii=False),
    ])
