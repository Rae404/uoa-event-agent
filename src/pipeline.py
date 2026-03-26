"""Pipeline: scrape → deduplicate → filter expired → time window → score → export."""

import logging
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from typing import List, Optional
from zoneinfo import ZoneInfo

# Auckland timezone (handles DST automatically)
NZ_TZ = ZoneInfo("Pacific/Auckland")
NZST = NZ_TZ  # keep alias for backward compat

from config.settings import EVENT_WINDOW_DAYS, TIME_WINDOW_THIS_WEEK, TIME_WINDOW_NEXT_WEEK
from src.content_generator import (
    generate_content as gen_content,
    generate_brief_summaries,
    generate_weekly_roundup,
)
from src.exporter import export_events, export_content
from src.history import filter_new_events
from src.models import Event
from src.notion_sync import sync_to_notion, NotionSync
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
    weekly_roundup: Optional[str] = None,
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

    # 2b. Filter online-only events (not relevant for local audience)
    before = len(all_events)
    all_events = _filter_online(all_events)
    online = before - len(all_events)
    if online:
        logger.info(f"Filtered {online} online-only events")

    # 3. Deduplicate (cross-source)
    all_events = deduplicate(all_events)
    logger.info(f"After dedup: {len(all_events)}")

    # 3b. Time window filter — only keep events within EVENT_WINDOW_DAYS
    # IMPORTANT: This must happen BEFORE historical dedup, so that far-future
    # events are not marked as "seen" and can be picked up when they enter the window.
    all_events = _filter_time_window(all_events)

    # 3c. Historical dedup — skip events seen in previous runs
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

    # 7b. Generate brief summaries for B-level events (template, no API)
    b_summaries = generate_brief_summaries(all_events)
    content_map.update(b_summaries)

    # 8. Push to Notion (if enabled and configured)
    if push_notion:
        sync_to_notion(all_events, content_map=content_map)

        # Mark expired events in Notion
        _mark_expired_in_notion()

    # 8b. Weekly roundup (if enabled)
    if weekly_roundup and push_notion:
        _push_weekly_roundup(all_events, mode=weekly_roundup)

    # 9. Export
    path = export_events(all_events, output_path)

    # Summary
    priority_counts = {}
    for e in all_events:
        p = e.priority or "unscored"
        priority_counts[p] = priority_counts.get(p, 0) + 1
    logger.info(f"Final: {len(all_events)} events | Priorities: {priority_counts}")

    return path


def _filter_time_window(events: List[Event]) -> List[Event]:
    """Keep only events within EVENT_WINDOW_DAYS from now. Far-future events are excluded."""
    now = datetime.now(tz=NZST)
    cutoff = now + timedelta(days=EVENT_WINDOW_DAYS)
    result = []
    filtered = 0

    for event in events:
        if not event.date_start:
            result.append(event)
            continue
        event_date = event.date_start
        if event_date.tzinfo is None:
            event_date = event_date.replace(tzinfo=NZST)
        if event_date <= cutoff:
            result.append(event)
        else:
            filtered += 1

    if filtered:
        logger.info(f"Time window filter: removed {filtered} events beyond {EVENT_WINDOW_DAYS} days")
    return result


def _push_weekly_roundup(events: List[Event], mode: str = "full"):
    """Generate and push weekly roundup to Notion.

    Fetches ALL events for this week from Notion (not just the current pipeline
    batch) so the roundup is complete even when most events were seen in prior runs.
    """
    import os
    token = os.getenv("NOTION_TOKEN")
    db_id = os.getenv("NOTION_DATABASE_ID")
    if not token or not db_id:
        logger.info("Notion not configured, skipping weekly roundup")
        return

    try:
        syncer = NotionSync(token=token, database_id=db_id)
        notion_events = _fetch_notion_events_for_roundup(syncer)
        # Use Notion events if available, otherwise fall back to pipeline events
        roundup_source = notion_events if notion_events else events
        roundup_text = generate_weekly_roundup(roundup_source, mode=mode)
        if not roundup_text:
            return
        syncer.create_weekly_roundup_page(roundup_text, mode=mode)
    except Exception as e:
        logger.error(f"Failed to push weekly roundup to Notion: {e}")


def _fetch_notion_events_for_roundup(syncer: NotionSync) -> List[Event]:
    """Fetch all non-archived events from Notion DB and convert to Event objects."""
    import requests
    from src.models import Event as EventModel

    all_pages = []
    cursor = None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        resp = requests.post(
            f"{syncer.headers['Authorization']}".replace(f"Bearer ", "https://api.notion.com/v1/databases/{syncer.database_id}/query"),
            json=payload, headers=syncer.headers, timeout=30,
        ) if False else requests.post(
            f"https://api.notion.com/v1/databases/{syncer.database_id}/query",
            json=payload, headers=syncer.headers, timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        all_pages.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    events = []
    for page in all_pages:
        props = page.get("properties", {})

        # Skip weekly-roundup pages
        src_prop = props.get("Source", {})
        if src_prop.get("type") == "select" and src_prop.get("select"):
            if src_prop["select"].get("name") == "weekly-roundup":
                continue

        # Title
        title = ""
        for name, prop in props.items():
            if prop.get("type") == "title":
                title = "".join(p.get("plain_text", "") for p in prop.get("title", []))
                break
        if not title:
            continue

        # Date
        date_start = None
        date_prop = props.get("Date", {})
        if date_prop.get("type") == "date" and date_prop.get("date"):
            ds = date_prop["date"].get("start", "")
            if ds:
                try:
                    date_start = datetime.fromisoformat(ds)
                except ValueError:
                    pass
        if not date_start:
            continue

        # Location
        location = ""
        loc_prop = props.get("Location", {})
        if loc_prop.get("type") == "rich_text":
            location = "".join(p.get("plain_text", "") for p in loc_prop.get("rich_text", []))

        # Cost
        cost = "unknown"
        cost_prop = props.get("Cost", {})
        if cost_prop.get("type") == "rich_text":
            cost = "".join(p.get("plain_text", "") for p in cost_prop.get("rich_text", []))

        # Priority
        priority = None
        pri_prop = props.get("Priority", {})
        if pri_prop.get("type") == "select" and pri_prop.get("select"):
            priority = pri_prop["select"].get("name")

        # Source
        source_name = "unknown"
        if src_prop.get("type") == "select" and src_prop.get("select"):
            source_name = src_prop["select"].get("name", "unknown")

        # URL
        url = ""
        url_prop = props.get("URL", {})
        if url_prop.get("type") == "url":
            url = url_prop.get("url") or ""

        # Content (generated headline/body for roundup descriptions)
        content_text = ""
        content_prop = props.get("Content", {})
        if content_prop.get("type") == "rich_text":
            content_text = "".join(p.get("plain_text", "") for p in content_prop.get("rich_text", []))

        events.append(EventModel(
            title=title,
            date_start=date_start,
            location=location or None,
            description=content_text or None,
            source_url=url or "https://unknown",
            source_name=source_name,
            cost=cost or "unknown",
            priority=priority,
        ))

    logger.info(f"Fetched {len(events)} events from Notion for roundup")
    return events


def _mark_expired_in_notion():
    """Mark expired events in Notion as '已过期'."""
    import os
    token = os.getenv("NOTION_TOKEN")
    db_id = os.getenv("NOTION_DATABASE_ID")
    if not token or not db_id:
        return

    try:
        syncer = NotionSync(token=token, database_id=db_id)
        syncer.mark_expired_events()
    except Exception as e:
        logger.error(f"Failed to mark expired events in Notion: {e}")


_ONLINE_KEYWORDS = {"online", "virtual", "zoom", "teams", "webinar", "livestream"}


def _filter_online(events: List[Event]) -> List[Event]:
    """Remove online-only events (not relevant for local audience)."""
    result = []
    for event in events:
        loc = (event.location or "").lower()
        title = event.title.lower()
        if loc in _ONLINE_KEYWORDS or loc == "":
            # location is exactly "Online" / "Virtual" / empty — check title too
            if loc != "" or any(kw in title for kw in _ONLINE_KEYWORDS):
                continue
        elif any(kw in loc for kw in _ONLINE_KEYWORDS):
            # location contains online keyword, e.g. "Online event", "Zoom meeting"
            # But allow hybrid: "Auckland (Online)" has a real place too
            has_physical = any(
                part.strip() and not any(kw in part.strip().lower() for kw in _ONLINE_KEYWORDS)
                for part in loc.replace("(", ",").replace(")", ",").split(",")
            )
            if not has_physical:
                continue
        result.append(event)
    return result


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
