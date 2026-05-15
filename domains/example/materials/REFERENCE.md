# Reference materials

`domains/<name>/materials/` holds whatever supporting docs the agent reads
during SELF_MODIFICATION: references for any API or system it interacts
with, schema descriptions, conventions, primers, links to authoritative
external documentation.

Treat the threshold as "what a competent human onboard would need to figure
out the domain", not a step-by-step playbook. The agent reads these files
broadly at INITIALIZATION and again on demand when writing skills. Keep
them factual and concise; the agent's own `KNOWLEDGE.md` captures narrative
understanding, not these files.

One file per topic. ALL_CAPS markdown filenames for the committed docs.
Filename and content are both inputs to the agent's reasoning, so name them
descriptively.
