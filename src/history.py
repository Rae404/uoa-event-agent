"""Historical deduplication — track previously exported events to avoid re-pushing."""

import hashlib
import json
import logging
import os
from typing import List, Set

from config.settings import DEFAULT_OUTPUT_DIR
from src.models import Event

logger = logging.getLogger(__name__)

HISTORY_FILE = os.path.join(DEFAULT_OUTPUT_DIR, ".history.json")


def _event_fingerprint(event: Event) -> str:
    """Generate a stable fingerprint for an event based on title + date + source."""
    key = f"{event.title.lower().strip()}|{event.date_start.isoformat() if event.date_start else ''}|{event.source_name}"
    return hashlib.md5(key.encode()).hexdigest()


def load_history() -> Set[str]:
    """Load previously seen event fingerprints."""
    if not os.path.exists(HISTORY_FILE):
        return set()
    try:
        with open(HISTORY_FILE, "r") as f:
            data = json.load(f)
        return set(data.get("seen", []))
    except (json.JSONDecodeError, IOError):
        return set()


def save_history(seen: Set[str]):
    """Save event fingerprints to history file."""
    os.makedirs(os.path.dirname(HISTORY_FILE) or ".", exist_ok=True)
    with open(HISTORY_FILE, "w") as f:
        json.dump({"seen": list(seen)}, f)
    logger.debug(f"Saved {len(seen)} fingerprints to history")


def filter_new_events(events: List[Event]) -> List[Event]:
    """Filter out events that have been seen in previous runs.

    Returns only new events. Updates the history file with all current events.
    """
    seen = load_history()
    new_events = []
    current_fingerprints = set()

    for event in events:
        fp = _event_fingerprint(event)
        current_fingerprints.add(fp)
        if fp not in seen:
            new_events.append(event)

    if len(events) != len(new_events):
        logger.info(f"History filter: {len(new_events)} new, {len(events) - len(new_events)} previously seen")
    else:
        logger.info(f"History filter: all {len(new_events)} events are new")

    # Update history with current batch
    seen.update(current_fingerprints)
    save_history(seen)

    return new_events
