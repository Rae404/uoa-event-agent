"""Notion sync for supermarket promotional campaigns.

Each store has its own Notion database. This class handles one database at a time.
"""

import logging
from datetime import date
from typing import List, Optional, Set

import requests

from src.models import SupermarketPromo

logger = logging.getLogger(__name__)

NOTION_API_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class PromoNotionSync:
    """Push supermarket promos to a Notion database."""

    def __init__(self, token: str, database_id: str):
        self.token = token
        self.database_id = database_id
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_VERSION,
        }
        self._schema = None

    def _get_schema(self) -> dict:
        if self._schema is not None:
            return self._schema
        resp = requests.get(
            f"{NOTION_API_URL}/databases/{self.database_id}",
            headers=self.headers,
            timeout=15,
        )
        resp.raise_for_status()
        schema = {}
        for name, prop in resp.json().get("properties", {}).items():
            schema[name] = prop["type"]
        self._schema = schema
        return schema

    def _ensure_properties(self):
        """Create required properties if they don't exist."""
        schema = self._get_schema()
        needed = {
            "Date": "date",
            "URL": "url",
            "Description": "rich_text",
            "Status": "select",
            # Monetization tracking (Phase 2+)
            "Post URL": "url",
            "Engagement": "number",
            "Sponsored": "checkbox",
        }
        updates = {}
        for name, ptype in needed.items():
            if name not in schema:
                updates[name] = {ptype: {}}
        if not updates:
            return
        logger.info(f"Creating Notion properties: {list(updates.keys())}")
        resp = requests.patch(
            f"{NOTION_API_URL}/databases/{self.database_id}",
            json={"properties": updates},
            headers=self.headers,
            timeout=15,
        )
        resp.raise_for_status()
        self._schema = None
        self._get_schema()

    def _get_existing_titles(self) -> Set[str]:
        """Get existing page titles (lowered) to avoid duplicates."""
        titles = set()
        cursor = None
        while True:
            payload = {"page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor
            resp = requests.post(
                f"{NOTION_API_URL}/databases/{self.database_id}/query",
                json=payload,
                headers=self.headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            for page in data.get("results", []):
                if page.get("archived"):
                    continue
                props = page.get("properties", {})
                for _, prop in props.items():
                    if prop.get("type") == "title":
                        parts = prop.get("title", [])
                        text = "".join(p.get("plain_text", "") for p in parts)
                        if text:
                            titles.add(text.lower().strip())
                        break
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        logger.debug(f"Found {len(titles)} existing promos in Notion")
        return titles

    def sync_promos(self, promos: List[SupermarketPromo]) -> int:
        """Push promos to Notion. Returns count of created pages."""
        self._ensure_properties()
        if not promos:
            return 0

        existing = self._get_existing_titles()
        created = 0

        for promo in promos:
            title = self._format_title(promo)
            if title.lower().strip() in existing:
                continue
            try:
                self._create_page(promo, title)
                existing.add(title.lower().strip())
                created += 1
            except Exception as e:
                logger.error(f"Failed to create promo page '{title}': {e}")

        logger.info(f"Synced {created}/{len(promos)} promos")
        return created

    @staticmethod
    def _format_title(promo: SupermarketPromo) -> str:
        """Build Notion page title: campaign name + date range."""
        title = promo.title
        if promo.date_start and promo.date_end:
            title += f" ({promo.date_start.strftime('%m/%d')}-{promo.date_end.strftime('%m/%d')})"
        elif promo.date_start:
            title += f" ({promo.date_start.strftime('%m/%d')})"
        return title

    def _create_page(self, promo: SupermarketPromo, title: str):
        """Create a Notion page for a promo."""
        schema = self._get_schema()

        # Find the title property
        title_prop = "Name"
        for name, ptype in schema.items():
            if ptype == "title":
                title_prop = name
                break

        properties = {
            title_prop: {"title": [{"text": {"content": title}}]},
        }

        if "Date" in schema and promo.date_start:
            date_val = {"start": promo.date_start.isoformat()}
            if promo.date_end:
                date_val["end"] = promo.date_end.isoformat()
            properties["Date"] = {"date": date_val}

        if "URL" in schema and promo.url:
            properties["URL"] = {"url": promo.url}

        if "Description" in schema and promo.description:
            properties["Description"] = {
                "rich_text": [{"text": {"content": promo.description}}]
            }

        if "Status" in schema:
            properties["Status"] = {"select": {"name": "active"}}

        resp = requests.post(
            f"{NOTION_API_URL}/pages",
            json={
                "parent": {"database_id": self.database_id},
                "properties": properties,
            },
            headers=self.headers,
            timeout=15,
        )
        resp.raise_for_status()
        logger.debug(f"Created Notion page: {title}")

    def mark_expired(self) -> int:
        """Mark promos past their end date as expired."""
        today = date.today().isoformat()
        updated = 0
        cursor = None

        while True:
            payload = {
                "filter": {"property": "Status", "select": {"equals": "active"}},
                "page_size": 100,
            }
            if cursor:
                payload["start_cursor"] = cursor

            resp = requests.post(
                f"{NOTION_API_URL}/databases/{self.database_id}/query",
                json=payload,
                headers=self.headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            for page in data.get("results", []):
                props = page.get("properties", {})
                date_prop = props.get("Date", {})
                if date_prop.get("type") != "date" or not date_prop.get("date"):
                    continue
                end = (
                    date_prop["date"].get("end")
                    or date_prop["date"].get("start")
                    or ""
                )[:10]
                if end and end < today:
                    try:
                        requests.patch(
                            f"{NOTION_API_URL}/pages/{page['id']}",
                            json={
                                "properties": {
                                    "Status": {"select": {"name": "expired"}}
                                }
                            },
                            headers=self.headers,
                            timeout=15,
                        ).raise_for_status()
                        updated += 1
                    except Exception as e:
                        logger.error(f"Failed to expire page {page['id']}: {e}")

            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

        if updated:
            logger.info(f"Marked {updated} expired promos")
        return updated
