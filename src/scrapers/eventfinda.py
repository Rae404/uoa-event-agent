"""Eventfinda scraper — HTML card parsing + detail pages."""

import logging
import math
from typing import List, Optional

from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from config.settings import EVENTFINDA_BASE_URL, EVENTFINDA_SEARCH_URL
from src.models import Event
from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class EventfindaScraper(BaseScraper):
    source_name = "eventfinda"

    def scrape(self) -> List[Event]:
        events = []
        pages_needed = math.ceil(self.limit / 20)  # ~20 events per page

        for page_num in range(1, pages_needed + 1):
            url = EVENTFINDA_SEARCH_URL if page_num == 1 else f"{EVENTFINDA_SEARCH_URL}/page/{page_num}"
            try:
                resp = self.fetch(url)
                soup = BeautifulSoup(resp.text, "lxml")
                page_events = self._parse_listing(soup)
                if not page_events:
                    break
                events.extend(page_events)
                if len(events) >= self.limit:
                    break
            except Exception as e:
                logger.warning(f"Failed to fetch page {page_num}: {e}")
                break

        return events[: self.limit]

    def _parse_listing(self, soup: BeautifulSoup) -> List[Event]:
        """Parse event cards from a listing page."""
        events = []
        cards = soup.select("div.card")

        for card in cards:
            if len(events) >= self.limit:
                break
            try:
                event = self._parse_card(card)
                if event:
                    events.append(event)
            except Exception as e:
                logger.debug(f"Failed to parse card: {e}")
        return events

    def _parse_card(self, card) -> Optional[Event]:
        # Title + link
        title_el = card.select_one(".card-title a, h5 a, h4 a, h3 a")
        if not title_el:
            return None
        title = title_el.get_text(strip=True)
        link = title_el.get("href", "")
        if link and not link.startswith("http"):
            link = EVENTFINDA_BASE_URL + link
        if not title:
            return None

        # Date from meta-date text
        date_start = None
        date_el = card.select_one(".card-text.meta-date, .meta-date, .date")
        if date_el:
            date_text = date_el.get_text(strip=True)
            try:
                date_start = dateparser.parse(date_text, fuzzy=True)
            except (ValueError, TypeError):
                pass

        # Location / venue
        location = None
        text_els = card.select(".card-text")
        for el in text_els:
            if "meta-date" not in el.get("class", []):
                loc_text = el.get_text(strip=True)
                if loc_text and loc_text != title:
                    location = loc_text
                    break

        # Fetch detail page for richer data
        detail = self._fetch_detail(link) if link else {}

        return Event(
            title=title,
            date_start=detail.get("date_start") or date_start,
            date_end=detail.get("date_end"),
            location=detail.get("location") or location,
            description=detail.get("description"),
            source_url=link,
            source_name=self.source_name,
            cost=detail.get("cost", "unknown"),
            categories=detail.get("categories", []),
        )

    def _fetch_detail(self, url: str) -> dict:
        """Fetch the event detail page for additional info."""
        result = {}
        try:
            resp = self.fetch(url)
            soup = BeautifulSoup(resp.text, "lxml")

            # Location
            loc_el = soup.select_one(".location-name, .venue-name, [itemprop='location'] [itemprop='name']")
            if loc_el:
                result["location"] = loc_el.get_text(strip=True)

            # Dates from structured data
            start_el = soup.select_one("[itemprop='startDate'], time[datetime]")
            if start_el:
                dt_str = start_el.get("content") or start_el.get("datetime")
                if dt_str:
                    try:
                        result["date_start"] = dateparser.parse(dt_str)
                    except (ValueError, TypeError):
                        pass

            end_el = soup.select_one("[itemprop='endDate']")
            if end_el:
                dt_str = end_el.get("content") or end_el.get("datetime")
                if dt_str:
                    try:
                        result["date_end"] = dateparser.parse(dt_str)
                    except (ValueError, TypeError):
                        pass

            # Cost
            price_el = soup.select_one(".price, [itemprop='price'], .ticket-price")
            if price_el:
                price_text = price_el.get_text(strip=True).lower()
                if "free" in price_text:
                    result["cost"] = "free"
                else:
                    result["cost"] = price_text

            # Description
            desc_el = soup.select_one("[itemprop='description'], .event-description, .description")
            if desc_el:
                result["description"] = desc_el.get_text(strip=True)[:2000]

            # Categories
            cat_els = soup.select(".category a, .event-category a, [itemprop='category']")
            if cat_els:
                result["categories"] = [c.get_text(strip=True) for c in cat_els]

        except Exception as e:
            logger.debug(f"Detail fetch failed for {url}: {e}")

        return result
