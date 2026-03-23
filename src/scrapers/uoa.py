"""UoA Unievents scraper — direct REST API (no Playwright needed)."""

import logging
from typing import List

from dateutil import parser as dateparser

from src.models import Event
from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

UOA_API_URL = "https://apis.auckland.ac.nz/events-portal-access/v1/events"


class UoAScraper(BaseScraper):
    source_name = "uoa"

    def scrape(self) -> List[Event]:
        try:
            resp = self.fetch(UOA_API_URL)
            data = resp.json()
        except Exception as e:
            logger.error(f"UoA API request failed: {e}")
            return []

        if not isinstance(data, list):
            logger.warning(f"UoA API returned unexpected type: {type(data)}")
            return []

        logger.info(f"UoA API returned {len(data)} raw events")
        events = []
        for item in data:
            if len(events) >= self.limit:
                break
            event = self._parse_event(item)
            if event:
                events.append(event)

        return events

    def _parse_event(self, item: dict):
        """Parse a single event from the UoA API response."""
        try:
            name = (item.get("name") or "").strip()
            if not name:
                return None

            # Dates
            date_start = None
            date_end = None
            if item.get("startDateTime"):
                try:
                    date_start = dateparser.parse(item["startDateTime"])
                except (ValueError, TypeError):
                    pass
            if item.get("endDateTime"):
                try:
                    date_end = dateparser.parse(item["endDateTime"])
                except (ValueError, TypeError):
                    pass

            # Location — API provides a rich location object
            location = None
            loc = item.get("location")
            if isinstance(loc, dict):
                parts = []
                display = loc.get("displayName")
                if display:
                    parts.append(display)
                else:
                    if loc.get("name"):
                        parts.append(loc["name"])
                    if loc.get("address1"):
                        parts.append(loc["address1"])
                    if loc.get("city"):
                        parts.append(loc["city"])
                location = ", ".join(parts) if parts else None

            # Cost
            cost = "free"
            if item.get("isFree") is False:
                lowest = item.get("lowestPrice")
                highest = item.get("highestPrice")
                if lowest and highest and lowest != highest:
                    cost = f"${lowest}-${highest}"
                elif lowest:
                    cost = f"${lowest}"
                else:
                    cost = "paid"

            # URL
            url = item.get("url") or ""
            if not url and item.get("eventId"):
                url = f"https://unievents.auckland.ac.nz/event/{item['eventId']}"

            # Description — prefer summary (short), fall back to full description
            description = item.get("summary") or item.get("description") or ""
            if description:
                # Strip HTML tags if present
                if "<" in description:
                    from bs4 import BeautifulSoup
                    description = BeautifulSoup(description, "lxml").get_text(separator=" ", strip=True)
                description = description[:2000] or None

            # Categories
            categories = []
            if item.get("categoryName"):
                categories.append(item["categoryName"])
            if item.get("subcategoryName"):
                categories.append(item["subcategoryName"])

            return Event(
                title=name,
                date_start=date_start,
                date_end=date_end,
                location=location,
                description=description,
                source_url=url,
                source_name=self.source_name,
                cost=cost,
                categories=categories,
            )
        except Exception as e:
            logger.debug(f"Failed to parse UoA event: {e}")
            return None
