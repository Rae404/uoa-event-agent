"""Promo pipeline: scrape supermarket campaigns → dedup → detect new → notify → push to Notion → export JSON."""

import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional, Set

from config.settings import DEFAULT_OUTPUT_DIR
from src.models import SupermarketPromo
from src.scrapers.woolworths import WoolworthsScraper
from src.scrapers.paknsave import PaknsaveScraper
from src.scrapers.newworld import NewWorldScraper

logger = logging.getLogger(__name__)

PROMO_SCRAPER_MAP = {
    "woolworths": WoolworthsScraper,
    "paknsave": PaknsaveScraper,
    "newworld": NewWorldScraper,
}

ALL_PROMO_SOURCES = list(PROMO_SCRAPER_MAP.keys())

PROMO_HISTORY_FILE = os.path.join(DEFAULT_OUTPUT_DIR, ".promo_history.json")


# --- Promo history tracking ---

def _promo_key(promo: SupermarketPromo) -> str:
    """Generate a stable key for a promo: store|title_lower."""
    return f"{promo.store}|{promo.title.lower().strip()}"


def load_promo_history() -> Set[str]:
    """Load previously seen promo keys."""
    if not os.path.exists(PROMO_HISTORY_FILE):
        return set()
    try:
        with open(PROMO_HISTORY_FILE, "r") as f:
            data = json.load(f)
        return set(data.get("seen", []))
    except (json.JSONDecodeError, IOError):
        return set()


def save_promo_history(seen: Set[str]):
    """Save promo keys to history file."""
    os.makedirs(os.path.dirname(PROMO_HISTORY_FILE) or ".", exist_ok=True)
    with open(PROMO_HISTORY_FILE, "w") as f:
        json.dump({"seen": sorted(seen)}, f, ensure_ascii=False, indent=2)
    logger.debug(f"Saved {len(seen)} promo keys to history")


def detect_new_promos(promos: List[SupermarketPromo]) -> List[SupermarketPromo]:
    """Compare current promos against history, return only new ones. Updates history file."""
    seen = load_promo_history()
    new_promos = []
    current_keys = set()

    for promo in promos:
        key = _promo_key(promo)
        current_keys.add(key)
        if key not in seen:
            new_promos.append(promo)

    if new_promos:
        logger.info(f"Promo history: {len(new_promos)} new, {len(promos) - len(new_promos)} previously seen")
    else:
        logger.info(f"Promo history: no new promos (all {len(promos)} previously seen)")

    # Update history with current batch
    seen.update(current_keys)
    save_promo_history(seen)

    return new_promos


# --- Main pipeline ---

def run_promo_pipeline(
    sources: Optional[List[str]] = None,
    output_path: Optional[str] = None,
    push_notion: bool = False,
    send_notifications: bool = False,
) -> str:
    """Run the promo scraping pipeline. Returns the output file path."""
    if sources is None:
        sources = ALL_PROMO_SOURCES

    # 1. Scrape campaigns from each store
    all_promos: List[SupermarketPromo] = []
    for source in sources:
        scraper_cls = PROMO_SCRAPER_MAP.get(source)
        if not scraper_cls:
            logger.warning(f"Unknown promo source: {source}")
            continue
        scraper = scraper_cls()
        promos = scraper.run_promos()
        all_promos.extend(promos)
        for p in promos:
            logger.info(f"  {p.summary()}")

    logger.info(f"Total promos detected: {len(all_promos)}")

    if not all_promos:
        logger.warning("No promos scraped from any source")
        return _export([], output_path)

    # 2. Detect new promos (compare against history)
    new_promos = detect_new_promos(all_promos)

    # 3. Send notifications for new promos
    if send_notifications and new_promos:
        try:
            from src.notifier import notify_new_promos
            notify_new_promos(new_promos)
        except Exception as e:
            logger.error(f"Notification failed: {e}", exc_info=True)

    # 4. Push to Notion (each store → its own DB)
    if push_notion:
        _push_to_notion(all_promos)

    # 5. Export JSON
    return _export(all_promos, output_path)


def _push_to_notion(promos: List[SupermarketPromo]):
    """Push promos to store-specific Notion databases."""
    token = os.getenv("NOTION_TOKEN")
    if not token:
        logger.info("NOTION_TOKEN not set, skipping Notion sync")
        return

    db_map = {
        "woolworths": os.getenv("NOTION_WOOLWORTHS_DB_ID"),
        "paknsave": os.getenv("NOTION_PAKNSAVE_DB_ID"),
        "newworld": os.getenv("NOTION_NEWWORLD_DB_ID"),
    }

    # Group promos by store
    grouped: Dict[str, List[SupermarketPromo]] = {}
    for p in promos:
        grouped.setdefault(p.store, []).append(p)

    from src.promo_notion_sync import PromoNotionSync

    for store, store_promos in grouped.items():
        db_id = db_map.get(store)
        if not db_id:
            logger.info(f"No Notion DB for {store} (NOTION_{store.upper()}_DB_ID not set), skipping")
            continue
        try:
            syncer = PromoNotionSync(token=token, database_id=db_id)
            created = syncer.sync_promos(store_promos)
            syncer.mark_expired()
            logger.info(f"Notion [{store}]: {created} new promos pushed")
        except Exception as e:
            logger.error(f"Notion sync for {store} failed: {e}", exc_info=True)


def _export(promos: List[SupermarketPromo], output_path: Optional[str] = None) -> str:
    """Export promos to JSON file."""
    if not output_path:
        os.makedirs(DEFAULT_OUTPUT_DIR, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        output_path = os.path.join(DEFAULT_OUTPUT_DIR, f"promos_{today}.json")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    data = [p.model_dump(mode="json") for p in promos]
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    logger.info(f"Exported {len(promos)} promos to {output_path}")
    return output_path
