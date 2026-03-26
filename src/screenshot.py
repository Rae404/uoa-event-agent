"""Playwright-based screenshot capture for supermarket specials pages."""

import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

from config.settings import (
    DEFAULT_OUTPUT_DIR,
    WOOLWORTHS_SPECIALS_URL,
    NEWWORLD_SPECIALS_URL,
)

logger = logging.getLogger(__name__)

STORE_URLS = {
    "woolworths": WOOLWORTHS_SPECIALS_URL,
    "paknsave": "https://www.paknsave.co.nz/shop/deals",
    "newworld": NEWWORLD_SPECIALS_URL,
}


def take_screenshot(store: str, url: str, output_dir: str) -> Optional[str]:
    """Take a full-page screenshot of a supermarket specials page.

    Returns the screenshot file path, or None on failure.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("playwright not installed, skipping screenshot")
        return None

    filename = f"{store}_{datetime.now().strftime('%Y%m%d_%H%M')}.png"
    filepath = os.path.join(output_dir, filename)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                locale="en-NZ",
            )
            page = ctx.new_page()
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            # Scroll down to trigger lazy loading, then back to top
            page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            page.wait_for_timeout(1000)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(500)

            page.screenshot(path=filepath, full_page=True)
            browser.close()

        logger.info(f"Screenshot saved: {filepath}")
        return filepath

    except Exception as e:
        logger.error(f"Screenshot failed for {store}: {e}")
        return None


def take_all_screenshots(stores: Optional[List[str]] = None) -> List[str]:
    """Take screenshots for all specified stores.

    Returns list of saved file paths.
    """
    if stores is None:
        stores = list(STORE_URLS.keys())

    today = datetime.now().strftime("%m%d")
    output_dir = os.path.join(DEFAULT_OUTPUT_DIR, "screenshots", today)
    os.makedirs(output_dir, exist_ok=True)

    paths = []
    for store in stores:
        url = STORE_URLS.get(store)
        if not url:
            logger.warning(f"No URL configured for store: {store}")
            continue
        result = take_screenshot(store, url, output_dir)
        if result:
            paths.append(result)

    logger.info(f"Captured {len(paths)}/{len(stores)} screenshots")
    return paths
