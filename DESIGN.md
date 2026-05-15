# Stemcell Agent

> Note: the ideas, architectural decisions, experiments and learnings in this document are all mine. Claude was used to structure and edit the text due to time constraints, so the phrasing may read somewhat LLM-like.

## 1. Goal and framing

Our goal was to build a minimal agent that, given a class of problems, builds itself into a specialist through a bounded training phase, then freezes and is deployed for inference. We treat this as classical machine learning at a higher level of abstraction: the agent's framework is the architecture, the domain materials and target environment are the data, pass rate are the loss, the workspace contents at freeze are the trained weights, and inference is deployment on held-out instances. This lets us inherit train/test separation, deliberate freezing, and held-out evaluation rather than inventing analogues.

We were deliberately looking for the kind of task where specialization actually adds value. Generic tasks like writing code are not interesting here: large models already cover them well out of the box, so specialization adds little in this limited project. The cases that pay off are:

- Domains with **custom knowledge**: a company's internal taxonomy, a specific schema, a particular operational workflow.
- **Smaller scoped translation tasks**: learning to interact with a given tool via its API, turning free-text user requests into the right sequence of actions.
- Domains that **develop fast enough** that up-to-date information is not in the model's training data (a new API, an evolving regulation, recently-released tooling).

The aim of this submission is one solid demonstration on one such domain within a one-week scope.

## 2. Architecture

The agent is a single ReAct loop with ten base primitives (tools): file access, shell exec, HTTP request, web search, skill lookup, task submission, clean exit, and three phase-transition tools. We use the OpenAI Agents SDK because it abstracts the ReAct loop mechanics and makes the implementation cleaner. 

Above this fixed surface the agent authors *skills* under `agent_workspace/skills/<name>/`, each a `SKILL.md` runbook with optional Python scripts in `tools/`. Skills load progressively: only their one-line descriptions stay in the iteration prompt, and full content loads on demand when the agent reads a `SKILL.md` directly. Authored scripts are *not* registered as separate LLM-callable tools, because that flattens the menu and the model picks scripts by surface name match without consulting the skill that owns them. This two-layer reasoning was inspired by the architecture of pi.dev and OpenClaw.

We made a deliberate decision not to let the agent design its own architecture. The reasoning:

- **Historical**: Neural Architecture Search produced 1-3% gains at thousands of GPU-hours before transformers showed that general methods scaled with compute outperform structural search (Sutton's *Bitter Lesson*).
- **Empirical**: varying topology and content together makes regressions unattributable and invalidates accumulated skills.
- **Principled**: picking topology from a class description before any interaction is selection without information.
- **Functional completeness**: a ReAct loop with the fixed base tools, plus the agent's ability to compose Python scripts that can themselves issue LLM calls (via a `single_shot` helper), is expressive enough that common topology patterns become *content* rather than *structure*. A planner-executor split becomes a skill that writes a plan and consults it. MCTS becomes a deterministic Python procedure. Background monitoring becomes a `shell_exec`-spawned process.

Summary: a ReAct loop plus the base tools plus with arbitrary skills with code tools  and optional LLM calls inside generalizes, on demand, to any agent topology we considered.

### Phase model

```
                 INITIALIZATION
                       |
                       v
        SELF_MODIFICATION  <=>  SELF_TESTING
                                      |
                          declare_ready_for_inference
                                      |
                                      v
                                    READY
                                      |
                              (user save / reject)
                                      |
                                      v
                                 INFERENCE
```

- **INITIALIZATION**: a single bootstrap iteration. The agent reads `DESCRIPTION.md` and the supplied reference materials and writes a first `KNOWLEDGE.md`. No tasks are attempted yet.
- **SELF_MODIFICATION**: the writable training phase. The agent creates and revises skills, edits `KNOWLEDGE.md`, and populates `environment.yaml` with structured operational data its scripts will need. `submit_attempt` is not available here.
- **SELF_TESTING**: the workspace is frozen. The orchestrator presents one task per iteration; the agent solves it using its skills, knowledge, and base tools, then submits via `submit_attempt`, which verifies inline and returns `{passed: true|false}` in the same turn. The agent may transition back to SELF_MODIFICATION to revise, or stay in SELF_TESTING. When it is confident, it can declare ready before the iteration cap.
- **READY**: a human-in-the-loop pause after the agent calls `declare_ready_for_inference`. The user reviews and either saves the workspace to `trained_agents/<name>/` or rejects it back to SELF_MODIFICATION with a reason written into `FAILURE_LOG.md`.
- **INFERENCE**: the loaded-and-frozen runtime mode for a saved trained agent. Used by `stem evaluate` against held-out tasks and by `stem inference` for ad-hoc single-shot use. No transitions.

The SELF_MODIFICATION ⇄ SELF_TESTING alternation mirrors a forward/backward pass: each test cycle informs the next revision, and each revision is attributable to specific failures rather than to arbitrary tweaks during a long run.

## 3. Training

Training is failure-driven workspace mutation. The agent maintains four artifact types: skills (each a self-contained directory under `skills/`), `KNOWLEDGE.md` (narrative understanding, free and long), `environment.yaml` (structured operational data scripts can `yaml.safe_load`), and `FAILURE_LOG.md` (an editable record of what went wrong and why). The orchestrator enforces one behavioral invariant beyond the phase model: a failed attempt followed by no workspace change produces a forcing message in the next iteration's prompt, telling the agent that retrying without revising will produce the same failure.

**Task selection during SELF_TESTING** turned out to be a more consequential design decision than we expected. The naive approach is to let the agent choose its own next task. We tried this first; the agent gamed the ordering, repeatedly drawing tasks it already passed (keeping its self-reported pass rate up) and avoiding the ones it could not yet solve. Coverage of the task set collapsed. We then switched to uniform-random selection by the orchestrator. This forced skills to generalize across the full set, and turned each repeat draw into an incidental robustness check (the skill has to work reliably, not by chance once). The cost is loss of focus: once the agent has built skills that handle most of the set, the remaining failures appear only as often as random sampling surfaces them, and progress past a certain pass rate slows. Alternative policies are discussed in §7.

## 4. Evaluation domain and setup

The demonstration domain is a security analyst at "ExampleCo", a fictional EU mid-size company. Three OpenSearch indices (`auth_logs`, `network`, `dns`) feed six capabilities: detect security incidents, assess service health, audit user access, detect lateral movement, detect DNS tunneling, and investigate compromised accounts. The hard part is not detection in the abstract. Most incident shapes are familiar from general security knowledge. The hard part is recognizing which suspicious-looking activity is *documented* and should not be flagged. Six benign patterns recur in the logs:

- Partner monitoring probes that fail by design.
- Scheduled nightly backups.
- CFO mid-week travel from Singapore.
- Regional sales team travel across documented regions.
- An authorized weekly pen test that is identical-by-signature to credential stuffing.
- CI/CD nightly builds.

Each pattern is described in `DESCRIPTION.md` with enough detail that a competent analyst could write a recognition rule. The trained agent must do so.

The evaluation set is held out by *data*, not by capability. Same task templates, same verifier functions, two different calendar weeks of synthetic logs generated deterministically from different seeds by a data generator. An agent that learns the general patterns passes both weeks; an agent that memorizes specific values (a particular attacker IP, a particular compromised user) passes the training week and fails the eval week. There are 11 tasks per split, covering all six capabilities at multiple time-window granularities. 

The vanilla baseline is loaded from `trained_agents/vanilla/`: an empty workspace plus a `metadata.json` with an `is_baseline` flag. The orchestrator routes the baseline through the same evaluation code path as any saved trained agent, but `PathPolicy` refuses reads of the domain directory: the baseline cannot consult `DESCRIPTION.md`, only the task instruction and direct HTTP probes against the OpenSearch instance. This makes any pass-rate difference between trained and baseline attributable to specialization through workspace content rather than to information the baseline could have had at evaluation time. Both agents run with the same base model (`gpt-5`).

## 5. Results


| Metric            | Vanilla   | Trained       | Ratio        |
| ----------------- | --------- | ------------- | ------------ |
| Pass rate (of 11) | 7 (64%)   | **10 (91%)**  | +27 pp       |
| Wall time         | 996 s     | **331 s**     | 3.0x faster  |
| Total tokens      | 3,860,630 | **1,465,716** | 2.6x cheaper |
| LLM requests      | 171       | **90**        | 1.9x fewer   |


The trained agent won on three tasks the baseline could not: the full-week aggregate (4 distinct incidents to identify), the data-exfiltration task that requires connecting authentication and network signals, and the compromise investigation on the unaffected user. The one shared failure was the compromise investigation on the actually-compromised user: both agents identified the chain (unusual-geography login, then access to hosts outside the user's baseline, then large outbound transfer) and both graded its likelihood `medium` rather than the `high` the verifier expected.

Per-task cost is more telling than the average. The trained agent used 5 to 12 tool calls per task with low variance. The baseline used 4 to 25 with a heavy right tail, and two unsolved tasks ate 62% of its total token budget on dead-end re-attempts. Predictability matters in production: unpredictable cost is operationally worse than predictably-higher cost.

Training cost was 4.87M tokens across 51 iterations (about 23 minutes wall). Per inference cycle, the trained agent saves 2.39M tokens against the baseline. Training pays back after about two inference cycles.

We initially tried `gpt-5-mini`. Five runs on the same domain never exceeded a 0.25 pass rate. The workspaces always contained the same two errors: detector scripts queried fields with a `.keyword` suffix the index mapping does not have, and the audit script filtered on a `source.geo.continent` field that does not exist. The mini model's response to "missing X" from the verifier was almost always to elaborate `KNOWLEDGE.md` rather than to inspect the script that produced the empty result. The same setup with `gpt-5` reaches 0.91.

## 6. Findings

**Base-model choice dominated.** Same code, data, prompts: `gpt-5-mini` plateaus at 0.25; `gpt-5` reaches 0.91. Prompt and orchestrator changes from the same period moved the needle by single digits. The mini model was not bad at writing scripts; it was bad at self-correcting them. The value of this kind of specialization is bounded above by the base model's ability to debug its own work.

**Training is empirically stochastic.** One `gpt-5` run on the harder domain reached 1/11 and was halted by the architectural-failure stop at iteration 16. The immediate retry on identical setup reached 10/11, with no code, data, or sampling changes. A production deployment needs auto-rerun with a patience parameter; we did not build it.

**Test data construction is one of the most important tasks in a project like this.** The constraint that kept it tractable for us: test cases must only cover end-to-end logic, input and expected output. The internal path the agent takes is the agent's problem. If you start validating the path, you end up building the agent twice: once as the verifier and once as the actual agent. The whole exercise then collapses, because the verifier author has already done the work the agent was supposed to learn.

**Deterministic verifiers are often hard, or they leak the solution.** For fuzzy answer spaces (the compromise_01 likelihood: `medium` vs `high`), a deterministic check either over-constrains the agent's phrasing or under-constrains it by enumerating valid options, which effectively gives the agent the answer space. An LLM-as-judge that grades the agent's reasoning against expected criteria would be better for these cases. We kept the verifier deterministic for reproducibility within this project.

**The specialization story shape depends on base-model strength.** With a strong base model, the baseline solves a surprising fraction of the task set unaided. The accuracy gap shrinks; the efficiency gap holds. With a weaker base model, the accuracy gap dominates. A single-number pass-rate comparison can underplay the value of specialization paired with a strong model. Efficiency, predictability, and reliability on long-tail tasks are where specialization shows up consistently across base-model strengths.

## 7. Limitations and future work

**Verifier construction is the actual bottleneck.** Grading an agent on a real task often requires nearly being the agent. The verifier author has to know what the right answer looks like, which usually means building the detection logic themselves. Tasks with rich direct-feedback environments (a compiler, a code interpreter, a shell) sidestep the problem, but those are exactly the domains where current large models are already well-trained and the specialization story is least interesting. We worked around the issue with a deterministic generator that produces both data and expected answers from the same Python source, but that only works when we control the data. This is the dominant reason we limited the project to one domain.

**The most interesting deployment story is one we did not build.** Large models already handle broad common tasks well, so specializing them on those tasks has limited value. Where specialization pays is on **fast-moving or niche domains** whose information is not in the model's training data, and on **small local models** that lack capability for the target domain out of the box. The small-model case has a non-obvious twist: the small model's own reasoning during training may not be sharp enough to author the right tools, and pass/fail verifier feedback alone will not bridge that gap. The route around this is to use a large model only at training time as a *tool author*: the trained workspace freezes with those tools in place, and inference ships with only the small model executing them. Structurally similar to fine-tuning but cheaper to build, easier to update, and applicable in domains where curating a fine-tuning dataset is infeasible.

**Proactivity.** Current LLM agents either run continuously (expensive) or react to prompts. They cannot keep an eye on something and act when a condition fires. The base-tool surface includes `shell_exec`, so a trained agent can install file watchers, cron jobs, or webhooks during SELF_MODIFICATION and arrange for itself to be re-invoked. We expect the most economically interesting applications (monitoring, oncall triage, observability automation) to depend on this kind of proactive operation.

**Training strategy alternatives.** §3 explains why we chose orchestrator-driven uniform-random sampling and what its cost is (no focus on residual hard tasks). Other policies worth testing:

- **Hybrid sampling**: random until each task has been attempted N times, then focus on the residual.
- **LLM-as-teacher**: a stronger model than the agent that explains the issue with the agent's attempt when it fails, without giving the answer. Richer signal than pass/fail without leaking the solution.

