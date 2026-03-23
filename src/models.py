"""Pydantic data models for events."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class Event(BaseModel):
    """Unified event data model across all sources."""

    title: str
    date_start: Optional[datetime] = None
    date_end: Optional[datetime] = None
    location: Optional[str] = None
    description: Optional[str] = None
    source_url: str
    source_name: str  # eventfinda / eventbrite / meetup / uoa / council
    cost: str = "unknown"  # free / paid / $15 / unknown
    categories: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    score: Optional[int] = None  # 0-100
    priority: Optional[str] = None  # S / A / B / C
    scraped_at: datetime = Field(default_factory=datetime.now)

    def summary(self) -> str:
        """One-line summary for logging."""
        date_str = self.date_start.strftime("%m/%d") if self.date_start else "?"
        return f"[{self.source_name}] {date_str} | {self.title[:50]} | {self.cost}"
