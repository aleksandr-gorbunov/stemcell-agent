"""Per-task status tracker, written to agent_workspace/task_status.json so the agent sees it."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class TaskStatus:
    task_id: str
    capability: str
    instruction_summary: str
    attempts: int = 0
    last_outcome: str = "unattempted"   # "unattempted" | "passed" | "failed"
    last_attempt_at: str | None = None
    last_attempt_summary: str | None = None
    last_failure_details: str | None = None


class StatusTracker:
    def __init__(self, status_file: Path):
        self.status_file = status_file
        self.tasks: dict[str, TaskStatus] = {}

    def initialize(self, tasks: list[dict], *, preserve_existing: bool = False) -> None:
        """Seed task statuses for the given task list.

        If preserve_existing is True and the status file already exists, attempt
        history (attempts count, last_outcome, last_failure_details) for matching
        task IDs is carried over from the file. This is what `--resume` needs.
        """
        existing: dict[str, dict] = {}
        if preserve_existing and self.status_file.exists():
            try:
                data = json.loads(self.status_file.read_text())
                for entry in data.get("tasks", []):
                    existing[entry["task_id"]] = entry
            except (json.JSONDecodeError, KeyError):
                existing = {}

        self.tasks = {}
        for t in tasks:
            instr = t.get("instruction", "")
            st = TaskStatus(
                task_id=t["id"],
                capability=t.get("capability", ""),
                instruction_summary=(instr[:200] + ("…" if len(instr) > 200 else "")),
            )
            prior = existing.get(t["id"])
            if prior:
                st.attempts = int(prior.get("attempts", 0))
                st.last_outcome = prior.get("status", "unattempted")
                st.last_failure_details = prior.get("last_failure")
            self.tasks[t["id"]] = st
        self._persist()

    def record_attempt(self, task_id: str, passed: bool, summary: str, details: str = "") -> None:
        if task_id not in self.tasks:
            self.tasks[task_id] = TaskStatus(task_id=task_id, capability="?", instruction_summary="(unknown task id)")
        st = self.tasks[task_id]
        st.attempts += 1
        st.last_outcome = "passed" if passed else "failed"
        st.last_attempt_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        st.last_attempt_summary = summary
        st.last_failure_details = None if passed else details
        self._persist()

    def aggregate(self) -> dict:
        total = len(self.tasks)
        passed = sum(1 for t in self.tasks.values() if t.last_outcome == "passed")
        failed = sum(1 for t in self.tasks.values() if t.last_outcome == "failed")
        unattempted = sum(1 for t in self.tasks.values() if t.last_outcome == "unattempted")
        pass_rate = (passed / total) if total else 0.0
        attempted_at_least_once = total - unattempted
        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "unattempted": unattempted,
            "attempted_at_least_once": attempted_at_least_once,
            "pass_rate": pass_rate,
        }

    def per_task_view(self) -> list[dict]:
        """Compact view of all tasks for the agent (hides aggregate)."""
        return [
            {
                "task_id": t.task_id,
                "capability": t.capability,
                "instruction": t.instruction_summary,
                "attempts": t.attempts,
                "status": t.last_outcome,
                "last_failure": t.last_failure_details,
            }
            for t in self.tasks.values()
        ]

    def all_attempted(self) -> bool:
        return all(t.last_outcome != "unattempted" for t in self.tasks.values())

    def _persist(self) -> None:
        view = self.per_task_view()
        self.status_file.write_text(json.dumps({"tasks": view}, indent=2))
