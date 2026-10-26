"""Typed results of the Sponsor Renewal Desk: the reviewer's verdict and each pack's status."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PackStatus = Literal["queued", "researching", "drafting", "in_review", "awaiting_approval", "approved",
                     "rejected", "needs_edit", "failed"]


class PackCheck(BaseModel):
    label: str
    ok: bool
    detail: str = ""


class Review(BaseModel):
    """What ``check_pack`` found. ``passed`` only when every check is ok."""

    sponsor: str
    passed: bool
    checks: list[PackCheck] = Field(default_factory=list)

    @property
    def problems(self) -> list[str]:
        return [f"{c.label}: {c.detail}" if c.detail else c.label for c in self.checks if not c.ok]


class SponsorArgs(BaseModel):
    sponsor: str = Field(min_length=2, max_length=120, description="The sponsor's name, exactly as list_sponsors "
                                                                     "returned it")


class ListSponsorsArgs(BaseModel):
    tier: Literal["platinum", "gold", "silver", "bronze"] | None = Field(
        None, description="Only sponsors in this tier")
    sponsor: str | None = Field(None, max_length=120, description="Only sponsors whose name contains this text")
    top_n: int = Field(5, ge=1, le=5, description="How many sponsors to return (at most 5 per run)")


class PackArgs(BaseModel):
    sponsor: str = Field(min_length=2, max_length=120, description="The sponsor's name, exactly as list_sponsors "
                                                                     "returned it")
    markdown: str = Field(min_length=50, max_length=12_000, description="The full renewal pack in Markdown")


class FinalizeArgs(BaseModel):
    sponsor: str = Field(min_length=2, max_length=120, description="The sponsor's name, exactly as list_sponsors "
                                                                     "returned it")
    markdown: str | None = Field(None, max_length=12_000, description="Leave empty to save the reviewed draft "
                                                                        "as it is (a person's edit fills this in)")
