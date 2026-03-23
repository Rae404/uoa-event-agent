"""Abstract base class for all scrapers with rate limiting, retries, and session management."""

import logging
import time
from abc import ABC, abstractmethod
from typing import List

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config.settings import MAX_RETRIES, REQUEST_DELAY_SECONDS, REQUEST_TIMEOUT
from src.models import Event

logger = logging.getLogger(__name__)


class BaseScraper(ABC):
    """Base scraper with built-in rate limiting, retries, and session management."""

    source_name: str = "unknown"

    def __init__(self, limit: int = 50):
        self.limit = limit
        self.session = self._build_session()
        self._last_request_time = 0.0

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-NZ,en;q=0.9",
        })
        retry = Retry(
            total=MAX_RETRIES,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
        session.mount("http://", HTTPAdapter(max_retries=retry))
        return session

    def _rate_limit(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < REQUEST_DELAY_SECONDS:
            time.sleep(REQUEST_DELAY_SECONDS - elapsed)
        self._last_request_time = time.time()

    def fetch(self, url: str, **kwargs) -> requests.Response:
        """GET with rate limiting and timeout."""
        self._rate_limit()
        logger.debug(f"Fetching: {url}")
        response = self.session.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
        response.raise_for_status()
        return response

    @abstractmethod
    def scrape(self) -> List[Event]:
        """Scrape events from this source. Must be implemented by subclasses."""
        ...

    def run(self) -> List[Event]:
        """Run the scraper with error handling."""
        logger.info(f"[{self.source_name}] Starting scrape (limit={self.limit})")
        try:
            events = self.scrape()
            logger.info(f"[{self.source_name}] Scraped {len(events)} events")
            return events
        except Exception as e:
            logger.error(f"[{self.source_name}] Scrape failed: {e}", exc_info=True)
            return []
