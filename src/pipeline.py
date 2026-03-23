"""Pipeline: scrape → deduplicate → filter expired → score → export."""

import logging
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from typing import List, Optional

# Auckland timezone (NZST = UTC+12, NZDT = UTC+13)
NZST = timezone(timedelta(hours=12))

from src.content_generator import generate_content as gen_content
from src.exporter import export_events, export_content
from src.history import filter_new_events
from src.models import Event
from src.notion_sync import sync_to_notion
from src.scoring.ai_scorer import ai_score_events
from src.scoring.scorer import apply_rule_scores
from src.scrapers.auckland_council import AucklandCouncilScraper
from src.scrapers.eventbrite import EventbriteScraper
from src.scrapers.eventfinda import EventfindaScraper
from src.scrapers.meetup import MeetupScraper
from src.scrapers.uoa import UoAScraper

logger = logging.getLogger(__name__)

SCRAPER_MAP = {
    "eventfinda": EventfindaScraper,
    "eventbrite": EventbriteScraper,
    "meetup": MeetupScraper,
    "uoa": UoAScraper,
    "council": AucklandCouncilScraper,
}

ALL_SOURCES = list(SCRAPER_MAP.keys())


def run_pipeline(
    sources: Optional[List[str]] = None,
    use_ai: bool = True,
    limit: int = 50,
    output_path: Optional[str] = None,
    verbose: bool = False,
    generate_content: bool = False,
    push_notion: bool = False,
) -> str:
    """Run the full scraping pipeline. Returns the output file path."""

    if sources is None:
        sources = ALL_SOURCES

    # 1. Scrape from all requested sources
    all_events: List[Event] = []
    for source in sources:
        scraper_cls = SCRAPER_MAP.get(source)
        if not scraper_cls:
            logger.warning(f"Unknown source: {source}")
            continue
        scraper = scraper_cls(limit=limit)
        events = scraper.run()
        all_events.extend(events)

    logger.info(f"Total raw events: {len(all_events)}")

    if not all_events:
        logger.warning("No events scraped from any source")
        return export_events([], output_path)

    # 2. Filter expired events (past events waste AI budget)
    before = len(all_events)
    all_events = _filter_expired(all_events)
    expired = before - len(all_events)
    if expired:
        logger.info(f"Filtered {expired} expired events")

    # 3. Deduplicate (cross-source)
    all_events = deduplicate(all_events)
    logger.info(f"After dedup: {len(all_events)}")

    # 3b. Historical dedup — skip events seen in previous runs
    all_events = filter_new_events(all_events)

    # 4. Rule-based pre-scoring
    all_events = apply_rule_scores(all_events)

    # 5. AI scoring (if enabled)
    if use_ai:
        all_events = ai_score_events(all_events)

    # 6. Sort by score (highest first)
    all_events.sort(key=lambda e: e.score or 0, reverse=True)

    # 7. Generate content for high-priority events
    content_map = {}
    if use_ai:
        content_results = gen_content(all_events)
        if content_results:
            content_path = (output_path or "").replace(".json", "_content.json")
            if not content_path or content_path == "_content.json":
                content_path = None
            export_content(content_results, content_path)
            # Build lookup for Notion sync
            for item in content_results:
                content_map[item["event_title"]] = item.get("generated", {})

    # 8. Push to Notion (if enabled and configured)
    if push_notion:
        sync_to_notion(all_events, content_map=content_map)

    # 9. Export
    path = export_events(all_events, output_path)

    # Summary
    priority_counts = {}
    for e in all_events:
        p = e.priority or "unscored"
        priority_counts[p] = priority_counts.get(p, 0) + 1
    logger.info(f"Final: {len(all_events)} events | Priorities: {priority_counts}")

    return path


def _filter_expired(events: List[Event]) -> List[Event]:
    """Remove events that have already ended."""
    now = datetime.now(tz=NZST)
    result = []
    for event in events:
        # Use end_date if available, otherwise start_date
        event_end = event.date_end or event.date_start
        if event_end is None:
            # No date — keep it (can't determine if expired)
            result.append(event)
            continue
        # Make timezone-aware if naive
        if event_end.tzinfo is None:
            event_end = event_end.replace(tzinfo=NZST)
        if event_end >= now:
            result.append(event)
    return result


def deduplicate(events: List[Event]) -> List[Event]:
    """Remove duplicate events based on title similarity + date overlap."""
    unique: List[Event] = []

    for event in events:
        is_dup = False
        for existing in unique:
            if _is_duplicate(event, existing):
                is_dup = True
                # Keep the one with more info
                if _richness(event) > _richness(existing):
                    unique.remove(existing)
                    unique.append(event)
                break
        if not is_dup:
            unique.append(event)

    if len(events) != len(unique):
        logger.info(f"Dedup removed {len(events) - len(unique)} duplicates")
    return unique


def _is_duplicate(a: Event, b: Event) -> bool:
    """Two events are duplicates if titles are >80% similar AND dates overlap."""
    title_sim = SequenceMatcher(None, a.title.lower(), b.title.lower()).ratio()
    if title_sim < 0.8:
        return False

    # If either has no date, rely on title similarity alone
    if not a.date_start or not b.date_start:
        return title_sim > 0.9

    # Check date overlap (same day)
    return a.date_start.date() == b.date_start.date()


def _richness(event: Event) -> int:
    """Score how 'rich' an event's data is — prefer more complete entries."""
    score = 0
    if event.description:
        score += len(event.description)
    if event.location:
        score += 10
    if event.date_start:
        score += 10
    if event.cost != "unknown":
        score += 5
    if event.categories:
        score += 5
    return score
