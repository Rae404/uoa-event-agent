"""Woolworths NZ promotional campaign detector.

Strategy: Internal product API → aggregate campaign tags.
Playwright fallback for page-level extraction.
"""

import logging
import re
from collections import Counter
from datetime import date, timedelta
from typing import List

from config.settings import WOOLWORTHS_API_URL, WOOLWORTHS_SPECIALS_URL
from src.models import SupermarketPromo
from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# Tags that appear every week — not worth a separate entry
_REGULAR_TAGS = frozenset({
    "half price", "low price", "prices dropped", "new", "multi buy",
    "was", "combo", "save", "special", "isspecial",
})


class WoolworthsScraper(BaseScraper):
    source_name = "woolworths"

    def scrape(self) -> list:
        return []

    def run_promos(self) -> List[SupermarketPromo]:
        """Detect current Woolworths promotional campaigns."""
        try:
            promos = self._try_api()
            if promos:
                return promos
            return self._try_playwright()
        except Exception as e:
            logger.error(f"Woolworths promo scrape failed: {e}")
            return []

    def _this_week(self):
        today = date.today()
        mon = today - timedelta(days=today.weekday())
        sun = mon + timedelta(days=6)
        return mon, sun

    def _try_api(self) -> List[SupermarketPromo]:
        """Hit the internal API and aggregate campaign tags."""
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "x-requested-with": "OnlineShopping.WebApp",
        })
        self.fetch("https://www.woolworths.co.nz/")

        resp = self.fetch(
            WOOLWORTHS_API_URL,
            params={
                "target": "specials",
                "size": "120",
                "page": "1",
                "inStockProductsOnly": "true",
            },
        )
        data = resp.json()
        items = data.get("products", {}).get("items", [])
        total = data.get("products", {}).get("totalItems", len(items))

        if not items:
            return []

        # Aggregate product tags → campaign names
        tags = Counter()
        for item in items:
            tag = item.get("productTag") or {}
            additional = tag.get("additionalTag") or {}
            name = (additional.get("name") or "").strip()
            if name:
                # Strip internal codes like "F2639_"
                name = re.sub(r'^[A-Z]\d+_', '', name)
                tags[name] += 1
            if tag.get("multiBuy"):
                tags["Multi Buy"] += 1

        mon, sun = self._this_week()

        # Always create the base weekly entry
        promos = [SupermarketPromo(
            title="本周特价",
            store="woolworths",
            date_start=mon,
            date_end=sun,
            url=WOOLWORTHS_SPECIALS_URL,
            description=f"共 {total} 件商品在促销",
        )]

        # Add entries for noteworthy campaigns (skip regular weekly tags)
        for name, count in tags.most_common():
            if name.lower() in _REGULAR_TAGS:
                continue
            promos.append(SupermarketPromo(
                title=name,
                store="woolworths",
                date_start=mon,
                date_end=sun,
                url=WOOLWORTHS_SPECIALS_URL,
                description=f"{count} 件商品",
            ))

        logger.info(f"Woolworths: {len(promos)} campaigns ({total} products total)")
        return promos

    def _try_playwright(self) -> List[SupermarketPromo]:
        """Fallback: visit page and extract campaign headings."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("Playwright not available for Woolworths fallback")
            return []

        mon, sun = self._this_week()
        promos = []

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    locale="en-NZ",
                )
                page = ctx.new_page()
                page.goto(
                    WOOLWORTHS_SPECIALS_URL,
                    timeout=45000,
                    wait_until="domcontentloaded",
                )
                page.wait_for_timeout(5000)

                texts = page.eval_on_selector_all(
                    "h1, h2, h3",
                    "els => els.map(e => e.textContent.trim())"
                    ".filter(t => t.length > 2 && t.length < 150)"
                )

                seen = set()
                for t in texts:
                    tl = t.lower().strip()
                    if tl in seen or tl in _REGULAR_TAGS:
                        continue
                    seen.add(tl)
                    promos.append(SupermarketPromo(
                        title=t.strip(),
                        store="woolworths",
                        date_start=mon,
                        date_end=sun,
                        url=WOOLWORTHS_SPECIALS_URL,
                    ))

                browser.close()
        except Exception as e:
            logger.error(f"Woolworths Playwright failed: {e}")

        # Always include base entry
        promos.insert(0, SupermarketPromo(
            title="本周特价",
            store="woolworths",
            date_start=mon,
            date_end=sun,
            url=WOOLWORTHS_SPECIALS_URL,
        ))
        return promos
