"""Rule-based pre-scoring — free, fast, no API calls needed."""

import logging
from datetime import datetime, timedelta
from typing import List

from config.settings import (
    POSITIVE_KEYWORDS,
    SCORE_FREE_EVENT,
    SCORE_KEYWORD_MATCH,
    SCORE_MINIMUM_THRESHOLD,
    SCORE_NEAR_UOA,
    SCORE_SOURCE_TRUST,
    SCORE_WITHIN_7_DAYS,
    UOA_LOCATION_KEYWORDS,
)
from src.models import Event

logger = logging.getLogger(__name__)


def rule_score(event: Event) -> int:
    """Calculate a rule-based score for an event. Returns 0-100."""
    score = 0
    text = f"{event.title} {event.description or ''} {event.location or ''}".lower()

    # Free event bonus
    if event.cost and event.cost.lower() == "free":
        score += SCORE_FREE_EVENT

    # Near UoA bonus
    location_text = (event.location or "").lower()
    if any(kw in location_text or kw in text for kw in UOA_LOCATION_KEYWORDS):
        score += SCORE_NEAR_UOA

    # Keyword matches
    matched_keywords = [kw for kw in POSITIVE_KEYWORDS if kw in text]
    score += len(matched_keywords) * SCORE_KEYWORD_MATCH

    # Source trust
    score += SCORE_SOURCE_TRUST.get(event.source_name, 0)

    # Time proximity — events within 7 days get a bonus
    if event.date_start:
        now = datetime.now(tz=event.date_start.tzinfo)
        days_away = (event.date_start - now).days
        if 0 <= days_away <= 7:
            score += SCORE_WITHIN_7_DAYS

    return min(score, 100)


def apply_rule_scores(events: List[Event]) -> List[Event]:
    """Apply rule-based scores and filter out low-scoring events."""
    scored = []
    filtered = 0
    for event in events:
        event.score = rule_score(event)
        if event.score >= SCORE_MINIMUM_THRESHOLD:
            scored.append(event)
        else:
            filtered += 1

    logger.info(f"Rule scoring: {len(scored)} passed, {filtered} filtered (below {SCORE_MINIMUM_THRESHOLD})")
    return scored
