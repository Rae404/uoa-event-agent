"""UoA Unievents scraper — Playwright for Ionic SPA, with API intercept fallback."""

import json
import logging
from typing import List, Optional

from dateutil import parser as dateparser

from src.models import Event
from src.scrapers.base import BaseScraper
from config.settings import UOA_EVENTS_URL

logger = logging.getLogger(__name__)


class UoAScraper(BaseScraper):
    source_name = "uoa"

    def scrape(self) -> List[Event]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
            return []

        events = []
        intercepted_events = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            # Intercept API calls to capture event data directly
            def handle_response(response):
                url = response.url
                if "api" in url.lower() and ("event" in url.lower() or "calendar" in url.lower()):
                    try:
                        data = response.json()
                        if isinstance(data, list):
                            intercepted_events.extend(data)
                        elif isinstance(data, dict):
                            items = data.get("events", data.get("items", data.get("results", [])))
                            if isinstance(items, list):
                                intercepted_events.extend(items)
                    except Exception:
                        pass

            page.on("response", handle_response)

            try:
                page.goto(UOA_EVENTS_URL, timeout=30000, wait_until="networkidle")
                page.wait_for_timeout(3000)  # extra wait for SPA rendering
            except Exception as e:
                logger.warning(f"Page load issue: {e}")

            # If we intercepted API data, parse it
            if intercepted_events:
                events = self._parse_api_data(intercepted_events)
            else:
                # Fallback: parse rendered HTML
                events = self._parse_rendered_html(page)

            browser.close()

        return events[: self.limit]

    def _parse_api_data(self, items: list) -> List[Event]:
        events = []
        for item in items:
            try:
                title = item.get("title") or item.get("name") or item.get("summary", "")
                if not title:
                    continue

                date_start = None
                date_end = None
                for date_key in ("startDate", "start_date", "dateStart", "start"):
                    if item.get(date_key):
                        try:
                            date_start = dateparser.parse(str(item[date_key]))
                            break
                        except (ValueError, TypeError):
                            pass
                for date_key in ("endDate", "end_date", "dateEnd", "end"):
                    if item.get(date_key):
                        try:
                            date_end = dateparser.parse(str(item[date_key]))
                            break
                        except (ValueError, TypeError):
                            pass

                location = item.get("location") or item.get("venue", {}).get("name") if isinstance(item.get("venue"), dict) else item.get("venue")

                url = item.get("url") or item.get("link") or ""
                if url and not url.startswith("http"):
                    url = "https://www.auckland.ac.nz" + url

                events.append(Event(
                    title=title.strip(),
                    date_start=date_start,
                    date_end=date_end,
                    location=location,
                    description=(item.get("description") or item.get("body") or "")[:2000] or None,
                    source_url=url,
                    source_name=self.source_name,
                    cost="free" if item.get("isFree", True) else "unknown",
                ))
            except Exception as e:
                logger.debug(f"Failed to parse UoA API item: {e}")
        return events

    def _parse_rendered_html(self, page) -> List[Event]:
        """Parse events from the rendered SPA HTML."""
        events = []
        try:
            cards = page.query_selector_all("ion-card, .event-card, article, .event-item, [class*='event']")
            if not cards:
                cards = page.query_selector_all("a[href*='event'], .card")

            for card in cards:
                try:
                    title_el = card.query_selector("h2, h3, h4, .event-title, ion-card-title")
                    if not title_el:
                        continue
                    title = title_el.inner_text().strip()
                    if not title:
                        continue

                    link_el = card.query_selector("a[href]") if card.evaluate("el => el.tagName") != "A" else card
                    url = link_el.get_attribute("href") if link_el else ""
                    if url and not url.startswith("http"):
                        url = "https://www.auckland.ac.nz" + url

                    date_el = card.query_selector("time, .event-date, .date, [class*='date']")
                    date_start = None
                    if date_el:
                        dt_attr = date_el.get_attribute("datetime")
                        if dt_attr:
                            try:
                                date_start = dateparser.parse(dt_attr)
                            except (ValueError, TypeError):
                                pass

                    desc_el = card.query_selector("p, .event-description, .summary")
                    description = desc_el.inner_text().strip() if desc_el else None

                    events.append(Event(
                        title=title,
                        date_start=date_start,
                        location=None,
                        description=description[:2000] if description else None,
                        source_url=url,
                        source_name=self.source_name,
                        cost="free",
                    ))
                except Exception as e:
                    logger.debug(f"Failed to parse UoA card: {e}")
        except Exception as e:
            logger.warning(f"HTML parsing failed: {e}")
        return events
