"""Pydantic data models for events and supermarket promos."""

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class SupermarketPromo(BaseModel):
    """A supermarket promotional campaign / sale event."""

    title: str                                # "本周特价" or "Easter 3-Day Sale"
    store: str                                # woolworths / paknsave / newworld
    date_start: Optional[date] = None
    date_end: Optional[date] = None
    url: str
    description: Optional[str] = None
    scraped_at: datetime = Field(default_factory=datetime.now)

    def summary(self) -> str:
        """One-line summary for logging."""
        dates = ""
        if self.date_start and self.date_end:
            dates = f" ({self.date_start.strftime('%m/%d')}-{self.date_end.strftime('%m/%d')})"
        elif self.date_start:
            dates = f" ({self.date_start.strftime('%m/%d')})"
        return f"[{self.store}] {self.title}{dates}"


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
