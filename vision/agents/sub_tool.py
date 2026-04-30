"""Custom `agent.as_tool()` replacement with two upgrades over the SDK default:

1. Configurable `max_turns` — the SDK's `as_tool` calls `Runner.run` internally
   with `max_turns=10`, which is too tight for our specialists (each makes 5+
   tool calls and 2-3 reasoning rounds, easily 10+ turns total).
2. Structured-output friendly — when the sub-agent has `output_type` set, we
   serialize the typed result to JSON for the orchestrator (instead of the
   SDK's default of stringifying only the text portion).
"""
from __future__ import annotations

from typing import Any

from agents import Agent, Runner, RunContextWrapper, function_tool
from pydantic import BaseModel


def make_specialist_tool(
    agent: Agent,
    *,
    tool_name: str,
    tool_description: str,
    max_turns: int = 25,
):
    """Build a `function_tool` that runs `agent` end-to-end and returns its
    structured output as JSON to the parent agent.
    """

    @function_tool(name_override=tool_name, description_override=tool_description)
    async def run_specialist(ctx: RunContextWrapper[Any], input: str) -> str:
        result = await Runner.run(
            starting_agent=agent,
            input=input,
            context=ctx.context,
            max_turns=max_turns,
        )
        out = result.final_output
        if isinstance(out, BaseModel):
            return out.model_dump_json()
        if out is None:
            return ""
        return str(out)

    return run_specialist
