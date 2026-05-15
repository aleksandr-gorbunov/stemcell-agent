# <Domain name>

A one-paragraph framing of what the specialized agent for this domain should be
able to do. Who is the user. What is the input. What is the output. Why is the
domain hard enough to require specialization rather than a generic agent. The
agent reads this file at INITIALIZATION as its first impression of the work.

## Environment

What the agent talks to. URLs, hostnames, port numbers, identifiers, names of
env variables that hold credentials, schemas, indices, anything operational
the agent has to know in order to make calls. Keep this descriptive; the
agent extracts the structured parts into its own `environment.yaml` during
SELF_MODIFICATION.

## Schema notes

Field-level conventions, gotchas, things that are easy to get wrong if the
agent guesses from prior knowledge instead of reading the actual data.
Naming exceptions, units, encoding rules, anything where the obvious
interpretation is not the correct one in this domain.

## Domain-specific knowledge the agent has to internalize

The non-obvious knowledge a specialized agent needs in order to answer well.
Recurring patterns, business rules, exceptions, expected-but-unusual cases,
anything that would mislead a generic agent that only has the raw data. With
enough detail that a reader could write a recognition rule.

## Expected answer formats

For each capability listed below, the exact shape the agent should return.
Field names, enumerated values, what an empty answer looks like. The
verifier reads these as ground truth.

## Capabilities

The specialized agent must support:

- <capability_id_1>: One-line description of what this capability does.
- <capability_id_2>: Another capability.
- <capability_id_3>: A third.

The orchestrator parses bullet items under this exact heading. Each line is
`- <id>: <description>`. The stop criterion uses the id list to check that
every capability has been exercised at least once during training.
