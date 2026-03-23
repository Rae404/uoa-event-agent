"""Notion database sync — push scored events to a Notion database."""

import logging
import os
from typing import List, Optional

import requests
from dotenv import load_dotenv

from src.models import Event

logger = logging.getLogger(__name__)

load_dotenv()

NOTION_API_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotionSync:
    """Push events to a Notion database."""

    def __init__(self, token: Optional[str] = None, database_id: Optional[str] = None):
        self.token = token or os.getenv("NOTION_TOKEN")
        self.database_id = database_id or os.getenv("NOTION_DATABASE_ID")

        if not self.token or not self.database_id:
            raise ValueError(
                "Notion credentials not configured. "
                "Set NOTION_TOKEN and NOTION_DATABASE_ID in .env"
            )

        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_VERSION,
        }

    def sync_events(self, events: List[Event], priorities: Optional[List[str]] = None) -> int:
        """Push events to Notion. Returns count of successfully created pages.

        Args:
            events: Scored events to push
            priorities: Only push these priorities (default: S, A, B)
        """
        if priorities is None:
            priorities = ["S", "A", "B"]

        target = [e for e in events if e.priority in priorities]
        if not target:
            logger.info("No events match target priorities for Notion sync")
            return 0

        created = 0
        for event in target:
            try:
                self._create_page(event)
                created += 1
            except Exception as e:
                logger.error(f"Failed to push '{event.title}' to Notion: {e}")

        logger.info(f"Notion sync: {created}/{len(target)} events pushed")
        return created

    def _create_page(self, event: Event):
        """Create a Notion page for an event."""
        properties = {
            "Name": {"title": [{"text": {"content": event.title}}]},
            "Source": {"select": {"name": event.source_name}},
            "Priority": {"select": {"name": event.priority or "C"}},
            "Score": {"number": event.score or 0},
            "Cost": {"rich_text": [{"text": {"content": event.cost}}]},
            "URL": {"url": event.source_url or None},
        }

        # Optional date
        if event.date_start:
            date_val = {"start": event.date_start.isoformat()}
            if event.date_end:
                date_val["end"] = event.date_end.isoformat()
            properties["Date"] = {"date": date_val}

        # Optional location
        if event.location:
            properties["Location"] = {"rich_text": [{"text": {"content": event.location[:2000]}}]}

        # Tags as multi-select
        if event.tags:
            properties["Tags"] = {"multi_select": [{"name": t} for t in event.tags[:5]]}

        body = {
            "parent": {"database_id": self.database_id},
            "properties": properties,
        }

        # Add description as page content
        if event.description:
            body["children"] = [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"text": {"content": event.description[:2000]}}]
                    },
                }
            ]

        resp = requests.post(
            f"{NOTION_API_URL}/pages",
            json=body,
            headers=self.headers,
            timeout=15,
        )
        resp.raise_for_status()
        logger.debug(f"Created Notion page for: {event.title}")


def sync_to_notion(events: List[Event], priorities: Optional[List[str]] = None) -> int:
    """Convenience function to sync events to Notion.

    Returns count of created pages, or 0 if not configured.
    """
    token = os.getenv("NOTION_TOKEN")
    db_id = os.getenv("NOTION_DATABASE_ID")

    if not token or not db_id:
        logger.info("Notion not configured (NOTION_TOKEN / NOTION_DATABASE_ID not set), skipping sync")
        return 0

    syncer = NotionSync(token=token, database_id=db_id)
    return syncer.sync_events(events, priorities)
