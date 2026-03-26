"""Eventbrite scraper — parse __SERVER_DATA__ embedded JSON."""

import json
import logging
import re
from datetime import datetime
from typing import List, Optional

from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from config.settings import EVENTBRITE_SEARCH_URL
from src.models import Event
from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class EventbriteScraper(BaseScraper):
    source_name = "eventbrite"

    def scrape(self) -> List[Event]:
        resp = self.fetch(EVENTBRITE_SEARCH_URL)
        soup = BeautifulSoup(resp.text, "lxml")

        # Build a price lookup from __SERVER_DATA__ (has tags/price info)
        price_lookup = {}
        server_data = self._extract_server_data(resp.text)
        if server_data:
            price_lookup = self._build_price_lookup(server_data)

        # Primary: JSON-LD (richer data: 68 events with descriptions and addresses)
        events = self._parse_jsonld(soup, price_lookup)
        if events:
            return events[: self.limit]

        # Fallback: __SERVER_DATA__ buckets
        if server_data:
            events = self._parse_server_data(server_data)
            if events:
                return events[: self.limit]

        # Fallback: HTML cards
        events = self._parse_html_cards(soup)
        return events[: self.limit]

    def _extract_server_data(self, html: str) -> Optional[dict]:
        match = re.search(r'window\.__SERVER_DATA__\s*=\s*({.*?});\s*</script>', html, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                logger.debug("Failed to parse __SERVER_DATA__ JSON")
        return None

    def _build_price_lookup(self, data: dict) -> dict:
        """Build a URL→cost lookup from __SERVER_DATA__ tags/price info."""
        lookup = {}
        buckets = data.get("buckets", [])
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            for item in bucket.get("events", []):
                url = item.get("url", "")
                if not url:
                    continue

                cost = "unknown"
                if item.get("is_free"):
                    cost = "free"
                elif item.get("ticket_availability"):
                    ticket = item["ticket_availability"]
                    if isinstance(ticket, dict):
                        if ticket.get("is_free"):
                            cost = "free"
                        elif ticket.get("minimum_ticket_price"):
                            min_p = ticket["minimum_ticket_price"]
                            cost = min_p.get("display", str(min_p.get("value", "unknown")))
                else:
                    tags = item.get("tags", [])
                    if isinstance(tags, list):
                        for t in tags:
                            name = t.get("display_name", "") if isinstance(t, dict) else str(t)
                            if "free" in name.lower():
                                cost = "free"
                                break

                if cost != "unknown":
                    lookup[url] = cost
        return lookup

    def _parse_server_data(self, data: dict) -> List[Event]:
        events = []
        seen_ids = set()

        # Events are in data["buckets"], each bucket has an "events" array
        buckets = data.get("buckets", [])
        if not buckets:
            # Try alternative structures
            for key, val in data.items():
                if isinstance(val, list) and val and isinstance(val[0], dict) and "events" in val[0]:
                    buckets = val
                    break

        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            event_list = bucket.get("events", [])
            for item in event_list:
                event_id = item.get("id", "")
                if event_id in seen_ids:
                    continue
                seen_ids.add(event_id)
                try:
                    event = self._item_to_event(item)
                    if event:
                        events.append(event)
                except Exception as e:
                    logger.debug(f"Failed to parse event item: {e}")

        logger.debug(f"Parsed {len(events)} unique events from __SERVER_DATA__ ({len(buckets)} buckets)")
        return events

    def _item_to_event(self, item: dict) -> Optional[Event]:
        title = item.get("name") or item.get("title", "")
        url = item.get("url", "")
        if not title:
            return None

        # Parse dates: start_date="2026-04-05", start_time="21:30"
        date_start = None
        date_end = None
        if item.get("start_date"):
            date_str = item["start_date"]
            if item.get("start_time"):
                date_str += f" {item['start_time']}"
            try:
                date_start = dateparser.parse(date_str)
            except (ValueError, TypeError):
                pass
        if item.get("end_date"):
            date_str = item["end_date"]
            if item.get("end_time"):
                date_str += f" {item['end_time']}"
            try:
                date_end = dateparser.parse(date_str)
            except (ValueError, TypeError):
                pass

        # Location from primary_venue
        location = None
        venue = item.get("primary_venue") or item.get("venue") or {}
        if isinstance(venue, dict):
            venue_name = venue.get("name", "")
            address = venue.get("address", {})
            if isinstance(address, dict):
                addr_display = address.get("localized_address_display", "")
                city = address.get("city", "")
                parts = [p for p in [venue_name, addr_display or city] if p]
                location = ", ".join(parts) if parts else None
            elif venue_name:
                location = venue_name

        # Cost — multiple detection methods
        cost = "unknown"

        # Method 1: is_free flag
        if item.get("is_free"):
            cost = "free"

        # Method 2: ticket_availability or min/max price
        if cost == "unknown":
            ticket = item.get("ticket_availability") or {}
            if isinstance(ticket, dict):
                if ticket.get("is_free"):
                    cost = "free"
                elif ticket.get("minimum_ticket_price"):
                    min_p = ticket["minimum_ticket_price"]
                    max_p = ticket.get("maximum_ticket_price", {})
                    min_val = min_p.get("display", min_p.get("value", ""))
                    max_val = max_p.get("display", max_p.get("value", "")) if isinstance(max_p, dict) else ""
                    if min_val and max_val and min_val != max_val:
                        cost = f"{min_val}-{max_val}"
                    elif min_val:
                        cost = str(min_val)

        # Method 3: tags
        if cost == "unknown":
            tags = item.get("tags", [])
            if isinstance(tags, list):
                tag_strs = []
                for t in tags:
                    if isinstance(t, dict):
                        tag_strs.append(t.get("display_name", "").lower())
                    elif isinstance(t, str):
                        tag_strs.append(t.lower())
                if any("free" in t for t in tag_strs):
                    cost = "free"

        if item.get("is_online_event"):
            if location:
                location += " (Online)"
            else:
                location = "Online"

        all_tags = item.get("tags", [])
        return Event(
            title=title.strip(),
            date_start=date_start,
            date_end=date_end,
            location=location,
            description=(item.get("summary") or item.get("description", ""))[:2000] or None,
            source_url=url,
            source_name=self.source_name,
            cost=cost,
            categories=[t.get("display_name", "") for t in all_tags if isinstance(t, dict) and t.get("display_name")][:5],
        )

    def _parse_jsonld(self, soup: BeautifulSoup, price_lookup: Optional[dict] = None) -> List[Event]:
        events = []
        if price_lookup is None:
            price_lookup = {}
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.string)
                if isinstance(data, dict) and data.get("@type") == "ItemList":
                    for list_item in data.get("itemListElement", []):
                        item = list_item.get("item", list_item)
                        if item.get("@type") == "Event":
                            event = self._jsonld_to_event(item, price_lookup)
                            if event:
                                events.append(event)
                elif isinstance(data, list):
                    for item in data:
                        if item.get("@type") == "Event":
                            event = self._jsonld_to_event(item, price_lookup)
                            if event:
                                events.append(event)
            except (json.JSONDecodeError, TypeError):
                continue
        return events

    def _jsonld_to_event(self, item: dict, price_lookup: Optional[dict] = None) -> Optional[Event]:
        title = item.get("name", "")
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

        # Location — combine name + street address
        location = None
        loc = item.get("location", {})
        if isinstance(loc, dict):
            addr = loc.get("address", {})
            if isinstance(addr, dict):
                street = addr.get("streetAddress", "")
                city = addr.get("addressLocality", "")
                parts = [p for p in [street, city] if p]
                location = ", ".join(parts) if parts else None
            if not location:
                location = loc.get("name")

        # Online event detection
        attendance = item.get("eventAttendanceMode", "")
        if "Online" in attendance:
            if location:
                location += " (Online)"
            else:
                location = "Online"

        # Cost — check price_lookup first, then JSON-LD offers
        url = item.get("url", "")
        cost = (price_lookup or {}).get(url, "unknown")

        if cost == "unknown":
            offers = item.get("offers", {})
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            if isinstance(offers, dict):
                price = offers.get("price", "")
                if price in ("0", "0.0", "0.00", ""):
                    cost = "free"
                elif price:
                    currency = offers.get("priceCurrency", "NZD")
                    cost = f"${price}" if currency in ("NZD", "USD", "AUD") else f"{price} {currency}"
                low = offers.get("lowPrice", "")
                high = offers.get("highPrice", "")
                if low and high and low != high:
                    cost = f"${low}-${high}"
                elif low:
                    cost = f"${low}" if low not in ("0", "0.0", "0.00") else "free"

        return Event(
            title=title.strip(),
            date_start=date_start,
            date_end=date_end,
            location=location,
            description=(item.get("description") or "")[:2000] or None,
            source_url=url,
            source_name=self.source_name,
            cost=cost,
        )

    def _parse_html_cards(self, soup: BeautifulSoup) -> List[Event]:
        events = []
        cards = soup.select("section.discover-vertical-event-card")
        for card in cards:
            try:
                title_el = card.select_one("h3")
                link_el = card.select_one("a.event-card-link[href]")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                url = link_el["href"].split("?")[0] if link_el else ""

                # Price from price wrapper
                cost = "unknown"
                price_el = card.select_one("[class*='priceWrapper'] p")
                if price_el:
                    price_text = price_el.get_text(strip=True).lower()
                    if "free" in price_text:
                        cost = "free"

                events.append(Event(
                    title=title,
                    source_url=url,
                    source_name=self.source_name,
                    cost=cost,
                ))
            except Exception as e:
                logger.debug(f"Failed to parse HTML card: {e}")
        return events
