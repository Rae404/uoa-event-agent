"""Meetup scraper — parse JSON-LD structured data."""

import json
import logging
from typing import List, Optional

from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from config.settings import MEETUP_SEARCH_URL
from src.models import Event
from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class MeetupScraper(BaseScraper):
    source_name = "meetup"

    def scrape(self) -> List[Event]:
        events = []
        resp = self.fetch(MEETUP_SEARCH_URL)
        soup = BeautifulSoup(resp.text, "lxml")

        # Primary: JSON-LD
        events = self._parse_jsonld(soup)

        # Fallback: parse Apollo state / __NEXT_DATA__
        if not events:
            events = self._parse_next_data(resp.text)

        # Fallback: HTML cards
        if not events:
            events = self._parse_html(soup)

        return events[: self.limit]

    def _parse_jsonld(self, soup: BeautifulSoup) -> List[Event]:
        events = []
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.string)
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get("@type") == "Event":
                        event = self._jsonld_to_event(item)
                        if event:
                            events.append(event)
            except (json.JSONDecodeError, TypeError):
                continue
        return events

    def _jsonld_to_event(self, item: dict) -> Optional[Event]:
        title = item.get("name", "").strip()
        if not title:
            return None

        date_start = None
        date_end = None
        try:
            if item.get("startDate"):
                date_start = dateparser.parse(item["startDate"])
            if item.get("endDate"):
                date_end = dateparser.parse(item["endDate"])
        except (ValueError, TypeError):
            pass

        location = None
        loc = item.get("location", {})
        if isinstance(loc, dict):
            addr = loc.get("address", {})
            if isinstance(addr, dict):
                location = addr.get("streetAddress") or addr.get("name")
            if not location:
                location = loc.get("name")

        cost = "unknown"
        offers = item.get("offers", {})
        if isinstance(offers, dict):
            price = str(offers.get("price", ""))
            if price in ("0", "0.0", ""):
                cost = "free"
            elif price:
                currency = offers.get("priceCurrency", "NZD")
                cost = f"${price} {currency}"
        elif isinstance(offers, list) and offers:
            price = str(offers[0].get("price", ""))
            if price in ("0", "0.0", ""):
                cost = "free"
            elif price:
                cost = f"${price}"

        return Event(
            title=title,
            date_start=date_start,
            date_end=date_end,
            location=location,
            description=(item.get("description") or "")[:2000] or None,
            source_url=item.get("url", ""),
            source_name=self.source_name,
            cost=cost,
        )

    def _parse_next_data(self, html: str) -> List[Event]:
        """Parse __NEXT_DATA__ JSON if available."""
        events = []
        import re
        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not match:
            return events

        try:
            data = json.loads(match.group(1))
            # Navigate Next.js data structure
            page_props = data.get("props", {}).get("pageProps", {})
            results = page_props.get("searchResults", []) or page_props.get("events", [])
            for item in results:
                event_data = item.get("node", item)
                title = event_data.get("title") or event_data.get("name", "")
                if not title:
                    continue
                date_start = None
                if event_data.get("dateTime"):
                    try:
                        date_start = dateparser.parse(event_data["dateTime"])
                    except (ValueError, TypeError):
                        pass

                venue = event_data.get("venue", {}) or {}
                location = venue.get("name")

                url = event_data.get("eventUrl") or ""
                if url and not url.startswith("http"):
                    url = "https://www.meetup.com" + url

                events.append(Event(
                    title=title.strip(),
                    date_start=date_start,
                    location=location,
                    description=(event_data.get("description") or "")[:2000] or None,
                    source_url=url,
                    source_name=self.source_name,
                    cost="free" if event_data.get("isFree") else "unknown",
                ))
        except (json.JSONDecodeError, KeyError) as e:
            logger.debug(f"__NEXT_DATA__ parse failed: {e}")

        return events

    def _parse_html(self, soup: BeautifulSoup) -> List[Event]:
        events = []
        cards = soup.select('[data-testid="categoryResults-eventCard"], .searchResult, a[href*="/events/"]')
        for card in cards:
            try:
                title_el = card.select_one("h2, h3, .text--labelSecondary")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                link = card.get("href") or ""
                if card.name != "a":
                    a = card.select_one("a[href*='/events/']")
                    link = a["href"] if a else ""
                if link and not link.startswith("http"):
                    link = "https://www.meetup.com" + link

                events.append(Event(
                    title=title,
                    source_url=link,
                    source_name=self.source_name,
                    cost="unknown",
                ))
            except Exception as e:
                logger.debug(f"Failed to parse Meetup HTML card: {e}")
        return events
