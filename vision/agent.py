"""CLI-friendly entry point. Wraps the orchestrator with a sync runner."""
import os

from agents import Runner
from dotenv import load_dotenv

from vision.agents import build_orchestrator

load_dotenv()


def run(query: str, *, verbose: bool = False) -> str:
    """Run a single user query through the VISION orchestrator and return the final text."""
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY not set. Copy .env.example to .env and add your key."
        )

    agent = build_orchestrator()
    result = Runner.run_sync(agent, query, max_turns=20)

    if verbose:
        for item in result.new_items:
            kind = type(item).__name__
            if kind == "ToolCallItem":
                raw = getattr(item, "raw_item", None)
                name = getattr(raw, "name", "?") if raw else "?"
                print(f"  → {name}")
            elif kind == "ToolCallOutputItem":
                output = getattr(item, "output", "")
                preview = str(output)[:120].replace("\n", " ")
                print(f"  ← {preview}{'…' if len(str(output)) > 120 else ''}")

    return result.final_output or ""
