"""Notion database sync — push scored events to a Notion database."""

import logging
import os
from typing import Dict, List, Optional

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
        self._db_schema = None

    def _get_db_schema(self) -> Dict[str, str]:
        """Fetch database schema to know which properties exist and their types."""
        if self._db_schema is not None:
            return self._db_schema

        resp = requests.get(
            f"{NOTION_API_URL}/databases/{self.database_id}",
            headers=self.headers,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        schema = {}
        for name, prop in data.get("properties", {}).items():
            schema[name] = prop["type"]

        self._db_schema = schema
        logger.debug(f"Notion DB schema: {schema}")
        return schema

    def _ensure_properties(self):
        """Add missing properties to the database."""
        schema = self._get_db_schema()

        # Properties we need: name → type
        needed = {
            "Source": "select",
            "Priority": "select",
            "Score": "number",
            "Cost": "rich_text",
            "URL": "url",
            "Date": "date",
            "Location": "rich_text",
            "Tags": "multi_select",
            "Content": "rich_text",
        }

        updates = {}
        for name, prop_type in needed.items():
            if name not in schema:
                updates[name] = {prop_type: {}}

        if not updates:
            return

        logger.info(f"Creating missing Notion properties: {list(updates.keys())}")
        resp = requests.patch(
            f"{NOTION_API_URL}/databases/{self.database_id}",
            json={"properties": updates},
            headers=self.headers,
            timeout=15,
        )
        resp.raise_for_status()
        # Refresh schema
        self._db_schema = None
        self._get_db_schema()

    def sync_events(self, events: List[Event], priorities: Optional[List[str]] = None,
                     content_map: Optional[Dict[str, dict]] = None) -> int:
        """Push events to Notion. Returns count of successfully created pages."""
        if priorities is None:
            priorities = ["S", "A", "B"]

        target = [e for e in events if e.priority in priorities]
        if not target:
            logger.info("No events match target priorities for Notion sync")
            return 0

        # Auto-create missing columns
        self._ensure_properties()

        created = 0
        for event in target:
            try:
                content = (content_map or {}).get(event.title)
                self._create_page(event, content)
                created += 1
            except Exception as e:
                logger.error(f"Failed to push '{event.title}' to Notion: {e}")

        logger.info(f"Notion sync: {created}/{len(target)} events pushed")
        return created

    def _create_page(self, event: Event, content: Optional[dict] = None):
        """Create a Notion page for an event."""
        schema = self._get_db_schema()

        # Find the title property (every DB has exactly one)
        title_prop = "Name"
        for name, ptype in schema.items():
            if ptype == "title":
                title_prop = name
                break

        properties = {
            title_prop: {"title": [{"text": {"content": event.title}}]},
        }

        if "Source" in schema:
            properties["Source"] = {"select": {"name": event.source_name}}
        if "Priority" in schema:
            properties["Priority"] = {"select": {"name": event.priority or "C"}}
        if "Score" in schema:
            properties["Score"] = {"number": event.score or 0}
        if "Cost" in schema:
            properties["Cost"] = {"rich_text": [{"text": {"content": event.cost}}]}
        if "URL" in schema:
            properties["URL"] = {"url": event.source_url or None}
        if "Date" in schema and event.date_start:
            date_val = {"start": event.date_start.isoformat()}
            if event.date_end:
                date_val["end"] = event.date_end.isoformat()
            properties["Date"] = {"date": date_val}
        if "Location" in schema and event.location:
            properties["Location"] = {"rich_text": [{"text": {"content": event.location[:2000]}}]}
        if "Tags" in schema and event.tags:
            properties["Tags"] = {"multi_select": [{"name": t} for t in event.tags[:5]]}

        # Add generated content headline to Content column
        if "Content" in schema and content:
            headline = content.get("headline", "")
            body_text = content.get("body", "")
            content_preview = f"【{headline}】{body_text}"[:2000] if headline else body_text[:2000]
            if content_preview:
                properties["Content"] = {"rich_text": [{"text": {"content": content_preview}}]}

        body = {
            "parent": {"database_id": self.database_id},
            "properties": properties,
        }

        # Page content: generated content first, then original description
        blocks = []
        if content:
            headline = content.get("headline", "")
            if headline:
                blocks.append({
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {
                        "rich_text": [{"text": {"content": headline}}]
                    },
                })
            body_text = content.get("body", "")
            if body_text:
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"text": {"content": body_text[:2000]}}]
                    },
                })
            hashtags = content.get("hashtags", [])
            if hashtags:
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"text": {"content": " ".join(hashtags)}}]
                    },
                })
            # Divider before original description
            if event.description:
                blocks.append({"object": "block", "type": "divider", "divider": {}})

        if event.description:
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"text": {"content": event.description[:2000]}}]
                },
            })

        if blocks:
            body["children"] = blocks

        resp = requests.post(
            f"{NOTION_API_URL}/pages",
            json=body,
            headers=self.headers,
            timeout=15,
        )
        resp.raise_for_status()
        logger.debug(f"Created Notion page for: {event.title}")


def sync_to_notion(events: List[Event], priorities: Optional[List[str]] = None,
                   content_map: Optional[Dict[str, dict]] = None) -> int:
    """Convenience function to sync events to Notion."""
    token = os.getenv("NOTION_TOKEN")
    db_id = os.getenv("NOTION_DATABASE_ID")

    if not token or not db_id:
        logger.info("Notion not configured, skipping sync")
        return 0

    syncer = NotionSync(token=token, database_id=db_id)
    return syncer.sync_events(events, priorities, content_map=content_map)
