"""Structured response types for specialists.

Every specialist returns a SpecialistResponse instead of free-form prose.
This forces:
- An explicit summary the orchestrator can quote
- Structured `key_metrics` for any numbers the user might want
- Citations (tool names + URLs) so the orchestrator can preserve traceability
- An explicit `errors` list — when a tool fails, the specialist must say so
  rather than silently working around it.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Citation(BaseModel):
    """Where a piece of information came from."""
    source: str = Field(
        ..., description="Tool name (e.g. 'get_quote') or article URL."
    )
    detail: str | None = Field(
        None, description="Short note on what was retrieved (e.g. ticker, query)."
    )


class SpecialistResponse(BaseModel):
    """The structured response every specialist returns to the orchestrator."""

    summary: str = Field(
        ...,
        description=(
            "Concise prose summary of findings, 100-300 words. The orchestrator "
            "may quote this directly or paraphrase. Do NOT include raw data dumps."
        ),
    )
    key_metrics: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Structured numbers/facts the orchestrator may surface. Flat dict — "
            "use stable keys like 'price', 'rsi_14', 'pe_ratio', '1m_return_pct'."
        ),
    )
    citations: list[Citation] = Field(
        default_factory=list,
        description="Tool calls and URLs that backed the findings.",
    )
    errors: list[str] = Field(
        default_factory=list,
        description=(
            "Any tool errors, missing data, or partial failures. MUST be populated "
            "if any tool returned an error or empty payload — the orchestrator and "
            "user need to know what failed. Empty list means everything succeeded."
        ),
    )
