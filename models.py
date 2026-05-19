"""Structured lead data models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Lead:
    """Validated business lead ready for export."""

    business_name: str = ""
    website: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    niche: str = ""
    tags: list[str] = field(default_factory=list)
    confidence_score: float = 0.0
    outreach_score: float = 0.0
    source_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def dedup_key(self) -> tuple[str, str, str]:
        return (
            self.business_name.lower().strip(),
            self.email.lower().strip(),
            self.website.lower().strip(),
        )

    def is_actionable(self) -> bool:
        return bool(
            self.business_name
            and (self.email or self.phone)
            and self.confidence_score >= 0.0
        )


@dataclass
class ScrapedPage:
    """Raw scrape result before AI extraction."""

    url: str
    html: str
    title: str = ""
    final_url: str = ""


@dataclass
class RawLeadCandidate:
    """Unvalidated lead from AI or heuristics."""

    business_name: str = ""
    website: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    niche: str = ""
    tags: list[str] = field(default_factory=list)
    confidence_score: float = 0.0
    source_url: str = ""

    def to_lead(self) -> Lead:
        return Lead(
            business_name=self.business_name,
            website=self.website,
            email=self.email,
            phone=self.phone,
            location=self.location,
            niche=self.niche,
            tags=list(self.tags),
            confidence_score=self.confidence_score,
            source_url=self.source_url,
        )
