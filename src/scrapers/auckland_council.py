"""Auckland Council events scraper."""

import json
import logging
from typing import List, Optional

from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from config.settings import AUCKLAND_COUNCIL_EVENTS_URL
from src.models import Event
from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class AucklandCouncilScraper(BaseScraper):
    source_name = "council"

    def scrape(self) -> List[Event]:
        events = []
        try:
            resp = self.fetch(AUCKLAND_COUNCIL_EVENTS_URL)
        except Exception as e:
            logger.warning(f"Council site may be blocking requests: {e}")
            return self._try_playwright()

        soup = BeautifulSoup(resp.text, "lxml")

        # Try JSON-LD first
        events = self._parse_jsonld(soup)
        if events:
            return events[: self.limit]

        # Fallback: HTML parsing
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
            location = loc.get("name") or loc.get("address", {}).get("streetAddress")

        cost = "unknown"
        offers = item.get("offers", {})
        if isinstance(offers, dict):
            price = str(offers.get("price", ""))
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

    def _parse_html(self, soup: BeautifulSoup) -> List[Event]:
        """Parse ourauckland.aucklandcouncil.govt.nz/events masonry card layout."""
        events = []
        # Primary selector for ourauckland site
        cards = soup.select(".article-tile--theme-events, .masonry-item .article-tile")
        if not cards:
            # Fallback generic selectors
            cards = soup.select("article, .listing-item, [class*='event']")

        for card in cards:
            if len(events) >= self.limit:
                break
            try:
                title_el = card.select_one(".article-tile__link, h2 a, h3 a, h2, h3, h4")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                if not title or len(title) < 3:
                    continue

                link_el = card.select_one("a.article-tile__link, a[href]")
                url = ""
                if link_el:
                    url = link_el.get("href", "")
                    if url and not url.startswith("http"):
                        url = "https://ourauckland.aucklandcouncil.govt.nz" + url

                date_start = None
                date_end = None
                date_el = card.select_one(".article-tile__date, time, .date")
                if date_el:
                    dt_str = date_el.get("datetime") or date_el.get_text(strip=True)
                    # Format: "18 Mar 2026 - 02 Apr 2026"
                    if " - " in dt_str:
                        parts = dt_str.split(" - ", 1)
                        try:
                            date_start = dateparser.parse(parts[0].strip())
                            date_end = dateparser.parse(parts[1].strip())
                        except (ValueError, TypeError):
                            pass
                    else:
                        try:
                            date_start = dateparser.parse(dt_str)
                        except (ValueError, TypeError):
                            pass

                # Fetch detail page for description and cost
                detail = self._fetch_detail(url) if url else {}

                events.append(Event(
                    title=title,
                    date_start=detail.get("date_start") or date_start,
                    date_end=detail.get("date_end") or date_end,
                    location=detail.get("location"),
                    description=detail.get("description"),
                    source_url=url,
                    source_name=self.source_name,
                    cost=detail.get("cost", "free"),
                    categories=detail.get("categories", []),
                ))
            except Exception as e:
                logger.debug(f"Failed to parse council card: {e}")
        return events

    def _fetch_detail(self, url: str) -> dict:
        """Fetch event detail page — prefer JSON-LD, fallback to HTML."""
        result = {}
        if not url:
            return result
        try:
            resp = self.fetch(url)
            soup = BeautifulSoup(resp.text, "lxml")

            # Try JSON-LD first — most reliable on council pages
            for script in soup.select('script[type="application/ld+json"]'):
                try:
                    data = json.loads(script.string)
                    if isinstance(data, dict) and data.get("@type") == "Event":
                        if data.get("description"):
                            # HTML entity decode
                            from html import unescape
                            result["description"] = unescape(data["description"])[:2000]
                        loc = data.get("location", {})
                        if isinstance(loc, dict):
                            name = loc.get("name", "")
                            addr = loc.get("address", {})
                            street = addr.get("streetAddress", "").strip() if isinstance(addr, dict) else ""
                            city = addr.get("addressLocality", "") if isinstance(addr, dict) else ""
                            parts = [p for p in [name, street, city] if p]
                            if parts:
                                result["location"] = ", ".join(parts)
                        if data.get("startDate"):
                            from html import unescape as hu
                            try:
                                result["date_start"] = dateparser.parse(hu(data["startDate"]))
                            except (ValueError, TypeError):
                                pass
                        if data.get("endDate"):
                            try:
                                result["date_end"] = dateparser.parse(hu(data["endDate"]))
                            except (ValueError, TypeError):
                                pass
                        # Cost from offers
                        offers = data.get("offers", {})
                        if isinstance(offers, dict):
                            price = str(offers.get("price", ""))
                            if price in ("0", "0.0", ""):
                                result["cost"] = "free"
                            elif price:
                                result["cost"] = f"${price}"
                        break
                except (json.JSONDecodeError, TypeError):
                    continue

            # Fallback: HTML parsing for description
            if "description" not in result:
                article = soup.select_one("article")
                if article:
                    paras = article.select("p")
                    desc = " ".join(p.get_text(strip=True) for p in paras[:5])
                    if desc:
                        result["description"] = desc[:2000]

            # Default cost for council events
            if "cost" not in result:
                result["cost"] = "free"

        except Exception as e:
            logger.debug(f"Council detail fetch failed for {url}: {e}")
        return result

    def _try_playwright(self) -> List[Event]:
        """Fallback to Playwright if requests are blocked."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("Playwright not available for council scraper fallback")
            return []

        events = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(AUCKLAND_COUNCIL_EVENTS_URL, timeout=30000)
                page.wait_for_timeout(3000)

                cards = page.query_selector_all("[class*='event'], article, .listing-item")
                for card in cards:
                    title_el = card.query_selector("h2, h3, h4, .title")
                    if not title_el:
                        continue
                    title = title_el.inner_text().strip()
                    link_el = card.query_selector("a[href]")
                    url = link_el.get_attribute("href") if link_el else ""
                    if url and not url.startswith("http"):
                        url = "https://ourauckland.aucklandcouncil.govt.nz" + url

                    events.append(Event(
                        title=title,
                        source_url=url,
                        source_name=self.source_name,
                        cost="unknown",
                    ))
                browser.close()
        except Exception as e:
            logger.error(f"Playwright fallback failed: {e}")
        return events[: self.limit]
