"""Base tools available to the stem in every phase.

These are instantiated per-iteration with workspace, domain, phase, and shared
iteration state bound. Phase-dependent restrictions are enforced here rather
than relied on the LLM to respect.
"""
from __future__ import annotations

import asyncio
import json
import os
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import httpx
from agents import FunctionTool

VerifyFn = Callable[[str, dict], "tuple[bool, str]"]


class Phase(str, Enum):
    INITIALIZATION = "initialization"
    SELF_MODIFICATION = "self_modification"
    SELF_TESTING = "self_testing"
    READY = "ready"
    INFERENCE = "inference"
    BASELINE = "baseline"


# Phases in which workspace writes are permitted.
WRITE_PHASES = {Phase.INITIALIZATION, Phase.SELF_MODIFICATION}

# Phases in which submit_attempt is permitted.
ATTEMPT_PHASES = {Phase.SELF_TESTING, Phase.INFERENCE, Phase.BASELINE}


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

class PathPolicy:
    """Decides whether an agent-provided path is allowed for a given operation."""

    def __init__(self, workspace: Path, domain_dir: Path, *, phase: Phase):
        self.workspace = workspace.resolve()
        self.domain_dir = domain_dir.resolve()
        self.phase = phase

    @property
    def writes_allowed(self) -> bool:
        return self.phase in WRITE_PHASES

    def _resolve(self, raw: str) -> Path:
        p = (self.workspace / raw).resolve() if not os.path.isabs(raw) else Path(raw).resolve()
        return p

    def can_read(self, raw: str) -> tuple[bool, Path | None, str]:
        # BASELINE has no domain-dir access so its before/after comparison
        # against the trained agent is strict: no peeking at DESCRIPTION.md.
        p = self._resolve(raw)
        if self.phase == Phase.BASELINE:
            allowed, suffix = (self.workspace,), "; BASELINE has access only to agent_workspace/"
        else:
            allowed, suffix = (self.workspace, self.domain_dir), " and the domain directory"
        for d in allowed:
            try:
                p.relative_to(d)
                return True, p, ""
            except ValueError:
                continue
        return False, None, f"read denied: {p} is outside agent_workspace{suffix}"

    def can_write(self, raw: str) -> tuple[bool, Path | None, str]:
        if not self.writes_allowed:
            return False, None, f"writes disabled in phase {self.phase.value}"
        p = self._resolve(raw)
        try:
            p.relative_to(self.workspace)
            return True, p, ""
        except ValueError:
            return False, None, f"write denied: {p} is outside agent_workspace"


# ---------------------------------------------------------------------------
# Shared iteration state populated by tool calls.
# ---------------------------------------------------------------------------

class IterationState:
    """Mutable state populated by tools during one Runner.run() invocation.

    Tool-call counts and the ordered trace are NOT tracked here; the SDK
    already records them in `RunResult.new_items`. The orchestrator extracts
    that information from the result after the run completes.
    """

    def __init__(self):
        self.attempts: list[dict] = []
        self.halt_reason: str | None = None
        self.requested_phase: Phase | None = None

    def record_attempt(
        self, *, task_id: str, summary: str, answer: Any,
        passed: bool | None = None, details: str = "",
    ) -> None:
        self.attempts.append({
            "task_id": task_id,
            "summary": summary,
            "answer": answer,
            "passed": passed,
            "details": details,
        })

    def halt(self, reason: str) -> None:
        self.halt_reason = reason

    def request_phase(self, phase: Phase) -> None:
        self.requested_phase = phase

    def reset(self) -> None:
        self.attempts.clear()
        self.halt_reason = None
        self.requested_phase = None


# ---------------------------------------------------------------------------
# File tools
# ---------------------------------------------------------------------------

def make_file_tools(policy: PathPolicy) -> list[FunctionTool]:
    async def _read_file(ctx, params_json: str) -> str:
        params = json.loads(params_json)
        ok, p, msg = policy.can_read(params["path"])
        if not ok:
            return json.dumps({"error": msg})
        try:
            return json.dumps({"content": p.read_text()})
        except FileNotFoundError:
            return json.dumps({"error": f"not found: {p}"})
        except UnicodeDecodeError:
            return json.dumps({"error": f"binary file (not utf-8): {p}"})

    async def _write_file(ctx, params_json: str) -> str:
        params = json.loads(params_json)
        ok, p, msg = policy.can_write(params["path"])
        if not ok:
            return json.dumps({"error": msg})
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(params["content"])
        return json.dumps({"ok": True, "bytes_written": len(params["content"])})

    async def _list_files(ctx, params_json: str) -> str:
        params = json.loads(params_json)
        ok, p, msg = policy.can_read(params["directory"])
        if not ok:
            return json.dumps({"error": msg})
        if not p.is_dir():
            return json.dumps({"error": f"not a directory: {p}"})
        out = []
        for child in sorted(p.iterdir()):
            out.append({
                "name": child.name,
                "is_dir": child.is_dir(),
                "size": child.stat().st_size if child.is_file() else None,
            })
        return json.dumps({"entries": out})

    return [
        FunctionTool(
            name="read_file",
            description="Read a UTF-8 text file. Allowed paths are inside agent_workspace/ or the domain directory.",
            params_json_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
            on_invoke_tool=_read_file,
        ),
        FunctionTool(
            name="write_file",
            description="Write (overwrite) a UTF-8 text file under agent_workspace/. Disabled outside write phases.",
            params_json_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            on_invoke_tool=_write_file,
        ),
        FunctionTool(
            name="list_files",
            description="List entries in a directory under agent_workspace/ or the domain directory.",
            params_json_schema={
                "type": "object",
                "properties": {"directory": {"type": "string"}},
                "required": ["directory"],
                "additionalProperties": False,
            },
            on_invoke_tool=_list_files,
        ),
    ]


# ---------------------------------------------------------------------------
# Shell tool
# ---------------------------------------------------------------------------

def make_shell_tool(policy: PathPolicy) -> FunctionTool:
    async def _shell_exec(ctx, params_json: str) -> str:
        params = json.loads(params_json)
        command = params["command"]
        timeout = int(params.get("timeout_seconds", 30))
        if not policy.writes_allowed:
            lowered = command.strip().lower()
            for bad in ("rm ", "mv ", ">", ">>", "touch ", "mkdir ", "chmod ", "chown "):
                if bad in lowered:
                    return json.dumps({"error": f"command blocked in phase {policy.phase.value}: contains '{bad.strip()}'"})
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(policy.workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return json.dumps({
                "exit_code": proc.returncode,
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
            })
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return json.dumps({"error": f"timeout after {timeout}s"})
        except Exception as exc:
            return json.dumps({"error": f"shell exec failed: {exc}"})

    return FunctionTool(
        name="shell_exec",
        description=(
            "Run a shell command with cwd inside agent_workspace/. Returns exit_code, stdout, stderr. "
            "Default timeout 30s. Mutation commands (rm, mv, redirects, touch, mkdir, chmod, chown) are "
            "blocked in phases where workspace writes are disabled."
        ),
        params_json_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        on_invoke_tool=_shell_exec,
    )


# ---------------------------------------------------------------------------
# HTTP tools
# ---------------------------------------------------------------------------

def make_http_tools() -> list[FunctionTool]:
    async def _http_request(ctx, params_json: str) -> str:
        params = json.loads(params_json)
        method = params["method"].upper()
        url = params["url"]
        headers = params.get("headers", {}) or {}
        body = params.get("body")
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                kwargs: dict[str, Any] = {"headers": headers}
                if body is not None:
                    if isinstance(body, (dict, list)):
                        kwargs["json"] = body
                    else:
                        kwargs["content"] = body
                r = await client.request(method, url, **kwargs)
                text = r.text
                if len(text) > 30_000:
                    text = text[:30_000] + "\n\n[truncated to keep context manageable; rerun with a more targeted query or aggregation if you need more]"
                return json.dumps({
                    "status": r.status_code,
                    "headers": dict(r.headers),
                    "text": text,
                })
        except Exception as exc:
            return json.dumps({"error": f"http request failed: {exc}"})

    return [
        FunctionTool(
            name="http_request",
            description="Generic HTTP request. Use this for talking to APIs.",
            params_json_schema={
                "type": "object",
                "properties": {
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"]},
                    "url": {"type": "string"},
                    "headers": {"type": "object"},
                    "body": {},
                },
                "required": ["method", "url"],
                "additionalProperties": False,
            },
            on_invoke_tool=_http_request,
            strict_json_schema=False,
        ),
    ]


def make_web_search_tool() -> FunctionTool | None:
    """Try to wire OpenAI's hosted WebSearchTool if the SDK exposes it."""
    try:
        from agents import WebSearchTool  # type: ignore
        return WebSearchTool()
    except Exception:
        return None


def make_list_skills_tool(workspace_root: Path) -> FunctionTool:
    """List skills currently in the agent's workspace, with name, one-line description, and script count."""
    async def _list(ctx, params_json: str) -> str:
        from stem.orchestrator.workspace import SKILLS_DIR, SKILL_DOC_FILE, SKILL_TOOLS_DIR
        skills_dir = workspace_root / SKILLS_DIR
        if not skills_dir.exists():
            return json.dumps({"skills": []})
        out = []
        for sd in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
            doc = sd / SKILL_DOC_FILE
            tools_dir = sd / SKILL_TOOLS_DIR
            py_count = sum(1 for _ in tools_dir.glob("*.py")) if tools_dir.exists() else 0
            description = ""
            if doc.exists():
                try:
                    for ln in doc.read_text().splitlines():
                        stripped = ln.strip()
                        if stripped and not stripped.startswith("#"):
                            description = stripped[:200]
                            break
                except Exception:
                    pass
            out.append({
                "name": sd.name,
                "description": description,
                "tool_count": py_count,
                "has_skill_doc": doc.exists(),
            })
        return json.dumps({"skills": out})

    return FunctionTool(
        name="list_skills",
        description=(
            "List the skills currently present in agent_workspace/skills/, with name, one-line "
            "description (from SKILL.md), and Python-tool count. Always reflects the live state of "
            "the workspace. Use this when you want to know what skills you have, especially after "
            "writing a new one."
        ),
        params_json_schema={"type": "object", "properties": {}, "additionalProperties": False},
        on_invoke_tool=_list,
    )


# ---------------------------------------------------------------------------
# Phase-restricted tools: submit_attempt and the three transition tools
# ---------------------------------------------------------------------------

def make_submit_attempt_tool(
    state: IterationState, phase: Phase, verify_fn: VerifyFn | None = None,
) -> FunctionTool | None:
    if phase not in ATTEMPT_PHASES:
        return None

    async def _submit(ctx, params_json: str) -> str:
        params = json.loads(params_json)
        task_id = params["task_id"]
        answer = params.get("answer")
        attempt_dict = {"task_id": task_id, "summary": params.get("summary", ""), "answer": answer}

        passed: bool | None = None
        details = ""
        if verify_fn is not None:
            passed, details = verify_fn(task_id, attempt_dict)

        state.record_attempt(
            task_id=task_id,
            summary=params.get("summary", ""),
            answer=answer,
            passed=passed,
            details=details,
        )
        return json.dumps({"task_id": task_id, "passed": passed})

    return FunctionTool(
        name="submit_attempt",
        description=(
            "Commit your attempt on a task. Provide the task_id, a short summary of what you did, and "
            "your final answer (as a JSON-compatible value if the task expects one). Returns "
            "`{passed: true|false}` synchronously: you learn whether the answer was correct in the "
            "same turn, without leaving the iteration."
        ),
        params_json_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "summary": {"type": "string"},
                "answer": {},
            },
            "required": ["task_id"],
            "additionalProperties": False,
        },
        on_invoke_tool=_submit,
        strict_json_schema=False,
    )


def make_halt_tool(state: IterationState) -> FunctionTool:
    async def _halt(ctx, params_json: str) -> str:
        params = json.loads(params_json)
        state.halt(params["reason"])
        return json.dumps({"received": True, "note": "Halt requested. The orchestrator will exit cleanly after this iteration."})

    return FunctionTool(
        name="halt_with_explanation",
        description=(
            "Signal that you have concluded the domain is unlearnable from the inputs given. "
            "The orchestrator logs the reason and exits cleanly. Use only with concrete evidence."
        ),
        params_json_schema={
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
            "additionalProperties": False,
        },
        on_invoke_tool=_halt,
    )


def make_transition_tools(state: IterationState, phase: Phase) -> list[FunctionTool]:
    tools: list[FunctionTool] = []

    if phase == Phase.SELF_MODIFICATION:
        async def _to_test(ctx, params_json: str) -> str:
            state.request_phase(Phase.SELF_TESTING)
            return json.dumps({"received": True, "next_phase": "self_testing"})

        tools.append(FunctionTool(
            name="enter_self_testing",
            description=(
                "Transition from SELF_MODIFICATION to SELF_TESTING. Your workspace becomes read-only and "
                "you may attempt tasks via submit_attempt. Call this when you want to see how your current "
                "workspace performs."
            ),
            params_json_schema={"type": "object", "properties": {}, "additionalProperties": False},
            on_invoke_tool=_to_test,
        ))

    if phase == Phase.SELF_TESTING:
        async def _to_mod(ctx, params_json: str) -> str:
            state.request_phase(Phase.SELF_MODIFICATION)
            return json.dumps({"received": True, "next_phase": "self_modification"})

        async def _to_ready(ctx, params_json: str) -> str:
            state.request_phase(Phase.READY)
            return json.dumps({
                "received": True,
                "next_phase": "ready",
                "note": "The orchestrator will pause for user review. The user may save you, reject (sending you back to SELF_MODIFICATION), or evaluate.",
            })

        tools.append(FunctionTool(
            name="enter_self_modification",
            description=(
                "Transition from SELF_TESTING back to SELF_MODIFICATION. You regain write access to "
                "agent_workspace/. Call this when testing has surfaced something you want to fix."
            ),
            params_json_schema={"type": "object", "properties": {}, "additionalProperties": False},
            on_invoke_tool=_to_mod,
        ))
        tools.append(FunctionTool(
            name="declare_ready_for_inference",
            description=(
                "Declare that your current workspace is good enough to be saved as a finalized trained agent. "
                "The orchestrator pauses you and hands off to the user. Callable only from SELF_TESTING."
            ),
            params_json_schema={"type": "object", "properties": {}, "additionalProperties": False},
            on_invoke_tool=_to_ready,
        ))

    return tools


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

def build_base_tools(
    policy: PathPolicy, iter_state: IterationState, verify_fn: VerifyFn | None = None,
) -> list[FunctionTool]:
    tools: list[FunctionTool] = []
    tools.extend(make_file_tools(policy))
    tools.append(make_list_skills_tool(policy.workspace))
    tools.append(make_shell_tool(policy))
    tools.extend(make_http_tools())
    web = make_web_search_tool()
    if web is not None:
        tools.append(web)
    submit = make_submit_attempt_tool(iter_state, policy.phase, verify_fn=verify_fn)
    if submit is not None:
        tools.append(submit)
    tools.append(make_halt_tool(iter_state))
    tools.extend(make_transition_tools(iter_state, policy.phase))
    return tools
