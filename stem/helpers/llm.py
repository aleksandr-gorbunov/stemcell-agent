"""LLM helper for use inside agent-authored Python scripts.

The agent imports `single_shot` from this module when one of its scripts needs
to make an LLM call (e.g., bulk classification, cheaper sub-task, structured
output). Synchronous because it's invoked from subprocess-launched scripts,
not from the async orchestrator.

The model is fixed by the user via the `STEMCELL_TOOL_MODEL` environment
variable; the agent cannot override it. This keeps cost and capability of
tool-internal LLM calls under the user's control independent of whatever the
main agent is running.

Example use inside `agent_workspace/skills/<skill>/tools/<name>.py`:

    import sys, json
    from stem.helpers.llm import single_shot

    # Unstructured output
    out = single_shot("Classify 'hello' as 'greeting' or 'question'. Reply with only the label.")

    # Structured output
    schema = {
        "type": "object",
        "properties": {
            "label": {"type": "string", "enum": ["greeting", "question"]},
            "confidence": {"type": "number"},
        },
        "required": ["label", "confidence"],
        "additionalProperties": False,
    }
    obj = single_shot("Classify 'hello'. Return label and confidence.", response_schema=schema)
    # obj is a dict matching the schema.
"""
from __future__ import annotations

import json
import os

from openai import OpenAI


_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


def _model() -> str:
    return os.environ.get("STEMCELL_TOOL_MODEL", "gpt-5-mini")


def single_shot(prompt: str, response_schema: dict | None = None) -> str | dict:
    """Make a single LLM completion call.

    Without `response_schema`, returns the LLM output as a string.
    With `response_schema`, the model is constrained to produce JSON matching
    the schema (passed via the Responses API's `text.format` with strict mode),
    and the parsed dict is returned.

    The model is read from `STEMCELL_TOOL_MODEL` env var and cannot be
    overridden by the caller.
    """
    client = _get_client()
    model = _model()

    if response_schema is None:
        response = client.responses.create(model=model, input=prompt)
        return response.output_text.strip()

    response = client.responses.create(
        model=model,
        input=prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "result",
                "strict": True,
                "schema": response_schema,
            }
        },
    )
    return json.loads(response.output_text)
