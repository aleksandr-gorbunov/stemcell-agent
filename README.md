# Stemcell Agent

An agent that specializes itself for a chosen domain. It learns the domain on its own and freezes into a usable trained specialist.

## Security note (read before running)

The agent has a `shell_exec` tool that runs arbitrary shell commands with the
permissions of the user who launched the orchestrator. There is no container,
no chroot, no firejail: the working directory is forced to `agent_workspace/`,
but a determined LLM (or a hostile domain definition, or a bad URL fetched
during SELF_MODIFICATION) can break out using absolute paths or by calling
tools other than `shell_exec`. Treat the agent as having the same trust level
as a script you would run yourself.

For a real experiment with a real target, prefer running inside a Docker
container that mounts `stem/` read-only and grants only the network access the
domain needs, or on a disposable VM. Local execution on your laptop is fine
for small targets you trust.

## Install

Python 3.11+. Uses [`uv`](https://docs.astral.sh/uv/) for dependency management.

```bash
uv sync
cp .env.example .env
# edit .env and set OPENAI_API_KEY
```

`uv sync` creates `.venv/`, resolves dependencies from `pyproject.toml`, and installs them. Prepend `uv run` to commands below to invoke them inside the project's environment without manual activation.

## Run

Train the stem on a domain:

```bash
python -m stem train --domain domains/<name>
```

The agent runs through INITIALIZATION → SELF_MODIFICATION ⇄ SELF_TESTING and
pauses when it declares ready for inference. At that point the workspace is
frozen and the orchestrator returns control to you.

While the agent is in READY, you can evaluate it without committing:

```bash
python -m stem evaluate --domain domains/<name>
```

If the evaluation looks good, save the agent as a permanent trained artifact:

```bash
python -m stem save --domain domains/<name> --name my_agent_v1
```

If the evaluation reveals problems, reject and resume training:

```bash
python -m stem reject --reason "missed the filter cases"
python -m stem train --domain domains/<name> --resume
```

Run the naive baseline for comparison (same agent code, empty workspace,
writes disabled, base tools only):

```bash
python -m stem baseline --domain domains/<name>
```

Load and evaluate a previously saved trained agent:

```bash
python -m stem inference --load trained_agents/my_agent_v1
```

Useful flags on `train` and `baseline`:

- `--model gpt-5.5`: override the main agent model (also settable via `STEMCELL_MODEL`)
- `--max-iterations 50`: cap on the SELF_MOD ⇄ SELF_TEST loop
- `--resume`: continue from the existing `agent_workspace/` state (preserves task statuses)

Environment variables (set in `.env`):

- `STEMCELL_MODEL`: main agent model (default `gpt-5.5`)
- `STEMCELL_TOOL_MODEL`: model used by the `single_shot` helper inside agent-authored scripts (default `gpt-5-mini`)
- `STEMCELL_TEST_MODEL`: model used by domain `tests.py` if it implements LLM-as-judge (default `gpt-5.5`)

## Layout

```
stem/                      orchestrator + agent code (all immutable at runtime)
  agent/                   what defines the agent: BOOTSTRAP_PROMPT.md, base tools
  orchestrator/            what runs the agent: phases, runner, workspace tracking, checkpoint creation, tests, stop criterion
  helpers/                 helpers the agent imports from its authored scripts (e.g. llm.single_shot)
  __main__.py              CLI entry point (invoked as `python -m stem`)

domains/                   domain definitions (one used per training run)

agent_workspace/           mutable scratch for the in-progress training run
checkpoints/               per-iteration snapshots of agent_workspace/
trained_agents/            saved completed runs (one subdirectory per save)

pyproject.toml             dependency manifest (managed by uv)
.env.example
```

## Adding a new domain

Create `domains/<name>/` with:

- `DESCRIPTION.md`: narrative description plus a `## Capabilities` section listing what the specialized agent must support (one bullet per capability, formatted `- <id>: <description>`)
- `materials/`: supporting docs the agent reads during SELF_MODIFICATION (optional)
- `tasks.yaml`: single set of test cases, each with an `id`, `capability`, `instruction`, and `verification: {function, ...}` reference
- `tests.py`: Python module exposing the test functions named in `tasks.yaml` (can include LLM-as-judge patterns via `STEMCELL_TEST_MODEL`; can be omitted if no automatic verification is feasible)

---

Code in this repository was developed with the assistance of Claude (Anthropic).
