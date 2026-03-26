"""PAK'nSAVE NZ promotional campaign detector.

Foodstuffs-family site behind Cloudflare — requires Playwright.
Visits the deals page and extracts campaign-level information.
"""

import logging
from datetime import date, timedelta
from typing import List

from src.models import SupermarketPromo
from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

PAKNSAVE_DEALS_URL = "https://www.paknsave.co.nz/shop/deals?pg=1"

# Generic navigation text to ignore
_IGNORE = frozenset({
    "deals", "specials", "special", "browse", "shop", "search",
    "categories", "menu", "home", "sign in", "register",
})


class PaknsaveScraper(BaseScraper):
    source_name = "paknsave"

    def scrape(self) -> list:
        return []

    def run_promos(self) -> List[SupermarketPromo]:
        """Detect current PAK'nSAVE promotional campaigns."""
        try:
            return self._scrape_page()
        except Exception as e:
            logger.error(f"PAK'nSAVE promo scrape failed: {e}")
            return self._fallback_entry()

    def _scrape_page(self) -> List[SupermarketPromo]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("Playwright not installed — cannot scrape PAK'nSAVE")
            return self._fallback_entry()

        mon, sun = self._this_week()
        promos = []
        product_count = 0

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

                # Count products from intercepted API
                def handle_response(response):
                    nonlocal product_count
                    if (
                        "search/paginated/products" in response.url
                        and response.status == 200
                    ):
                        try:
                            data = response.json()
                            product_count += len(data.get("products", []))
                        except Exception:
                            pass

                page.on("response", handle_response)
                page.goto(
                    PAKNSAVE_DEALS_URL,
                    timeout=30000,
                    wait_until="domcontentloaded",
                )
                page.wait_for_timeout(5000)

                # Extract headings and banner text
                texts = page.eval_on_selector_all(
                    "h1, h2, h3, [class*='banner'] *, [class*='hero'] *, "
                    "[class*='promo'] *, [class*='campaign'] *",
                    "els => els.map(e => e.textContent.trim())"
                    ".filter(t => t.length > 2 && t.length < 200)"
                )

                seen = set(_IGNORE)
                for t in texts:
                    tl = t.lower().strip()
                    if tl in seen or len(tl) < 3:
                        continue
                    seen.add(tl)
                    # Only add if it looks like a campaign
                    campaign_kw = (
                        "sale", "easter", "christmas", "save", "deal",
                        "offer", "bonus", "flash", "clearance", "day",
                        "week", "price", "half",
                    )
                    if any(kw in tl for kw in campaign_kw):
                        promos.append(SupermarketPromo(
                            title=t.strip(),
                            store="paknsave",
                            date_start=mon,
                            date_end=sun,
                            url=PAKNSAVE_DEALS_URL,
                        ))

                browser.close()
        except Exception as e:
            logger.error(f"PAK'nSAVE Playwright failed: {e}")

        # Always include the base weekly entry
        desc = f"共 {product_count} 件商品在促销" if product_count else None
        promos.insert(0, SupermarketPromo(
            title="本周特价",
            store="paknsave",
            date_start=mon,
            date_end=sun,
            url=PAKNSAVE_DEALS_URL,
            description=desc,
        ))

        logger.info(f"PAK'nSAVE: {len(promos)} campaigns")
        return promos

    def _this_week(self):
        today = date.today()
        mon = today - timedelta(days=today.weekday())
        sun = mon + timedelta(days=6)
        return mon, sun

    def _fallback_entry(self) -> List[SupermarketPromo]:
        mon, sun = self._this_week()
        return [SupermarketPromo(
            title="本周特价",
            store="paknsave",
            date_start=mon,
            date_end=sun,
            url=PAKNSAVE_DEALS_URL,
        )]
