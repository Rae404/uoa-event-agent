"""Pipeline: scrape → deduplicate → score → export."""

import logging
from difflib import SequenceMatcher
from typing import List, Optional

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

    # 2. Deduplicate (cross-source)
    all_events = deduplicate(all_events)
    logger.info(f"After dedup: {len(all_events)}")

    # 2b. Historical dedup — skip events seen in previous runs
    all_events = filter_new_events(all_events)

    # 3. Rule-based pre-scoring
    all_events = apply_rule_scores(all_events)

    # 4. AI scoring (if enabled)
    if use_ai:
        all_events = ai_score_events(all_events)

    # 5. Sort by score (highest first)
    all_events.sort(key=lambda e: e.score or 0, reverse=True)

    # 6. Generate content for high-priority events
    if generate_content and use_ai:
        content_results = gen_content(all_events)
        if content_results:
            content_path = (output_path or "").replace(".json", "_content.json")
            if not content_path or content_path == "_content.json":
                content_path = None
            export_content(content_results, content_path)

    # 7. Push to Notion (if enabled and configured)
    if push_notion:
        sync_to_notion(all_events)

    # 8. Export
    path = export_events(all_events, output_path)

    # Summary
    priority_counts = {}
    for e in all_events:
        p = e.priority or "unscored"
        priority_counts[p] = priority_counts.get(p, 0) + 1
    logger.info(f"Final: {len(all_events)} events | Priorities: {priority_counts}")

    return path


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
