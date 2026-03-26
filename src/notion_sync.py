"""Notion database sync — push scored events to a Notion database."""

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from src.models import Event

NZ_TZ = ZoneInfo("Pacific/Auckland")
NZST = NZ_TZ  # keep alias for backward compat

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

    @staticmethod
    def _normalise_to_nz(dt: datetime) -> datetime:
        """Normalise a datetime to NZ local time for Notion storage.

        - Naive → treat as NZ local, attach NZ tzinfo
        - +12/+13 (NZ-ish) → hour is already local, just fix offset
        - +00 or other → proper astimezone conversion
        """
        if dt.tzinfo is None:
            return dt.replace(tzinfo=NZ_TZ)
        offset_hours = dt.utcoffset().total_seconds() / 3600
        if 11 <= offset_hours <= 14:
            return dt.replace(tzinfo=NZ_TZ)
        return dt.astimezone(NZ_TZ)

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
            "Publish Status": "select",
            "Time Window": "select",
            "Publish Week": "rich_text",
            "Content Type": "select",
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

    def _get_existing_events(self) -> tuple:
        """Query Notion DB for existing URLs and title+date pairs to avoid duplicates.

        Returns (urls: set, title_dates: set) where title_dates contains
        normalized 'title_lower|date' strings for fuzzy cross-source matching.
        """
        urls = set()
        title_dates = set()
        start_cursor = None

        while True:
            payload = {"page_size": 100}
            if start_cursor:
                payload["start_cursor"] = start_cursor

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
                # Collect URL
                url_prop = props.get("URL", {})
                if url_prop.get("type") == "url" and url_prop.get("url"):
                    urls.add(url_prop["url"])
                # Collect title + date for cross-source dedup
                title_text = ""
                for name, prop in props.items():
                    if prop.get("type") == "title":
                        parts = prop.get("title", [])
                        title_text = "".join(p.get("plain_text", "") for p in parts)
                        break
                date_prop = props.get("Date", {})
                date_str = ""
                if date_prop.get("type") == "date" and date_prop.get("date"):
                    date_str = (date_prop["date"].get("start") or "")[:10]
                if title_text:
                    title_dates.add(f"{title_text.lower().strip()}|{date_str}")

            if not data.get("has_more"):
                break
            start_cursor = data.get("next_cursor")

        logger.debug(f"Found {len(urls)} existing URLs, {len(title_dates)} title+date pairs in Notion")
        return urls, title_dates

    def sync_events(self, events: List[Event], priorities: Optional[List[str]] = None,
                     content_map: Optional[Dict[str, dict]] = None) -> int:
        """Push events to Notion. Returns count of successfully created pages."""
        if priorities is None:
            priorities = ["S", "A", "B"]

        # Auto-create missing columns (always, even if no events match)
        self._ensure_properties()

        target = [e for e in events if e.priority in priorities]
        if not target:
            logger.info("No events match target priorities for Notion sync")
            return 0

        # Dedup: skip events already in Notion (by URL or title+date)
        existing_urls, existing_title_dates = self._get_existing_events()
        before = len(target)
        deduped = []
        for e in target:
            if e.source_url in existing_urls:
                continue
            date_str = e.date_start.strftime("%Y-%m-%d") if e.date_start else ""
            title_date_key = f"{e.title.lower().strip()}|{date_str}"
            if title_date_key in existing_title_dates:
                continue
            deduped.append(e)
        target = deduped
        skipped = before - len(target)
        if skipped:
            logger.info(f"Notion dedup: skipped {skipped} events already in database")

        if not target:
            logger.info("All events already exist in Notion, nothing to push")
            return 0

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
            ds = self._normalise_to_nz(event.date_start)
            date_val = {"start": ds.isoformat()}
            if event.date_end:
                de = self._normalise_to_nz(event.date_end)
                date_val["end"] = de.isoformat()
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

        # New columns: Publish Status, Time Window, Publish Week, Content Type
        if "Publish Status" in schema:
            properties["Publish Status"] = {"select": {"name": "待发布"}}
        if "Time Window" in schema:
            properties["Time Window"] = {"select": {"name": self._calc_time_window(event)}}
        if "Publish Week" in schema:
            now = datetime.now(tz=NZST)
            iso_cal = now.isocalendar()
            week_str = f"{iso_cal[0]}-W{iso_cal[1]:02d}"
            properties["Publish Week"] = {"rich_text": [{"text": {"content": week_str}}]}
        if "Content Type" in schema:
            # S/A with AI content → 单条推送; B with template summary → 简报
            has_ai_content = bool(content and len(content.get("body", "")) > 50)
            content_type = "单条推送" if has_ai_content else "简报"
            properties["Content Type"] = {"select": {"name": content_type}}

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

    @staticmethod
    def _calc_time_window(event: Event) -> str:
        """Calculate time window label based on calendar week (Mon-Sun)."""
        if not event.date_start:
            return "近期"
        now = datetime.now(tz=NZST)
        event_date = event.date_start
        if event_date.tzinfo is None:
            event_date = event_date.replace(tzinfo=NZST)
        else:
            event_date = event_date.astimezone(NZST)

        # Calendar week boundaries
        this_monday = (now - timedelta(days=now.weekday())).date()
        this_sunday = this_monday + timedelta(days=6)
        next_sunday = this_sunday + timedelta(days=7)

        edate = event_date.date()
        if this_monday <= edate <= this_sunday:
            return "本周"
        elif this_sunday < edate <= next_sunday:
            return "下周"
        else:
            return "近期"

    def mark_expired_events(self) -> int:
        """Mark expired events based on their current publish status.

        - 待发布 + date passed → 收集未发 (collected but never published)
        - 已发布 + date passed → 已过期   (published and event ended)

        Returns the number of pages updated.
        """
        self._ensure_properties()
        now = datetime.now(tz=NZST)
        today_iso = now.date().isoformat()

        # Each tuple: (source status to query, target status to set)
        transitions = [
            ("待发布", "收集未发"),
            ("已发布", "已过期"),
        ]

        updated = 0

        for source_status, target_status in transitions:
            payload = {
                "filter": {
                    "property": "Publish Status",
                    "select": {"equals": source_status},
                },
                "page_size": 100,
            }
            start_cursor = None

            while True:
                if start_cursor:
                    payload["start_cursor"] = start_cursor

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
                    date_val = date_prop["date"]
                    # Use end date if available, otherwise start date
                    end_str = date_val.get("end") or date_val.get("start")
                    if not end_str:
                        continue
                    # Compare date portion only
                    event_date_str = end_str[:10]
                    if event_date_str < today_iso:
                        try:
                            requests.patch(
                                f"{NOTION_API_URL}/pages/{page['id']}",
                                json={"properties": {
                                    "Publish Status": {"select": {"name": target_status}},
                                }},
                                headers=self.headers,
                                timeout=15,
                            ).raise_for_status()
                            updated += 1
                            logger.debug(f"Page {page['id']}: '{source_status}' → '{target_status}'")
                        except Exception as e:
                            logger.error(f"Failed to update page {page['id']} to '{target_status}': {e}")

                if not data.get("has_more"):
                    break
                start_cursor = data.get("next_cursor")

        if updated:
            logger.info(f"Marked {updated} expired events (待发布→收集未发, 已发布→已过期)")
        return updated

    def fix_legacy_expired_to_uncollected(self) -> int:
        """One-time fix: rename existing '已过期' pages to '收集未发'.

        Before the status split was introduced, all expired events (originally
        '待发布') were marked as '已过期'. This method corrects those legacy
        pages to '收集未发' since none of them were ever actually published.

        Returns the number of pages updated.
        """
        self._ensure_properties()
        payload = {
            "filter": {
                "property": "Publish Status",
                "select": {"equals": "已过期"},
            },
            "page_size": 100,
        }

        updated = 0
        start_cursor = None

        while True:
            if start_cursor:
                payload["start_cursor"] = start_cursor

            resp = requests.post(
                f"{NOTION_API_URL}/databases/{self.database_id}/query",
                json=payload,
                headers=self.headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            for page in data.get("results", []):
                try:
                    requests.patch(
                        f"{NOTION_API_URL}/pages/{page['id']}",
                        json={"properties": {
                            "Publish Status": {"select": {"name": "收集未发"}},
                        }},
                        headers=self.headers,
                        timeout=15,
                    ).raise_for_status()
                    updated += 1
                    logger.debug(f"Page {page['id']}: '已过期' → '收集未发' (legacy fix)")
                except Exception as e:
                    logger.error(f"Failed to fix page {page['id']}: {e}")

            if not data.get("has_more"):
                break
            start_cursor = data.get("next_cursor")

        if updated:
            logger.info(f"Fixed {updated} legacy '已过期' pages to '收集未发'")
        return updated

    def create_weekly_roundup_page(self, roundup_text: str, mode: str = "full") -> bool:
        """Create a weekly roundup page in Notion.

        Args:
            roundup_text: Formatted roundup content.
            mode: "full" or "supplement".
        """
        self._ensure_properties()
        schema = self._get_db_schema()

        now = datetime.now(tz=NZST)
        iso_cal = now.isocalendar()
        week_str = f"{iso_cal[0]}-W{iso_cal[1]:02d}"

        if mode == "supplement":
            title = f"周中补充 {week_str}"
            roundup_url = f"weekly-roundup-{week_str}-supplement"
        else:
            title = f"本周活动情报 {week_str}"
            roundup_url = f"weekly-roundup-{week_str}"

        # Check if roundup already exists — if so, overwrite it
        existing_page_id = self._find_roundup_page(roundup_url)

        # Build content blocks from roundup text
        blocks = self._text_to_blocks(roundup_text)

        if existing_page_id:
            # Overwrite: delete old blocks, append new ones
            self._replace_page_blocks(existing_page_id, blocks)
            logger.info(f"Updated existing weekly roundup page: {title}")
        else:
            # Create new page
            title_prop = "Name"
            for name, ptype in schema.items():
                if ptype == "title":
                    title_prop = name
                    break

            properties = {
                title_prop: {"title": [{"text": {"content": title}}]},
            }

            if "Priority" in schema:
                properties["Priority"] = {"select": {"name": "S"}}
            if "Publish Status" in schema:
                properties["Publish Status"] = {"select": {"name": "待发布"}}
            if "Time Window" in schema:
                properties["Time Window"] = {"select": {"name": "本周"}}
            if "Publish Week" in schema:
                properties["Publish Week"] = {"rich_text": [{"text": {"content": week_str}}]}
            if "Content Type" in schema:
                properties["Content Type"] = {"select": {"name": "周报合集"}}
            if "URL" in schema:
                properties["URL"] = {"url": roundup_url}
            if "Source" in schema:
                properties["Source"] = {"select": {"name": "weekly-roundup"}}

            body = {
                "parent": {"database_id": self.database_id},
                "properties": properties,
            }
            if blocks:
                body["children"] = blocks

            resp = requests.post(
                f"{NOTION_API_URL}/pages",
                json=body,
                headers=self.headers,
                timeout=15,
            )
            resp.raise_for_status()
            logger.info(f"Created weekly roundup page: {title}")
        return True

    def _find_roundup_page(self, roundup_url: str) -> Optional[str]:
        """Find existing roundup page by URL. Returns page_id or None."""
        resp = requests.post(
            f"{NOTION_API_URL}/databases/{self.database_id}/query",
            json={
                "filter": {"property": "URL", "url": {"equals": roundup_url}},
                "page_size": 1,
            },
            headers=self.headers,
            timeout=30,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if results and not results[0].get("archived"):
            return results[0]["id"]
        return None

    @staticmethod
    def _text_to_blocks(text: str) -> list:
        """Split text into Notion paragraph blocks, each ≤ 1900 chars."""
        blocks = []
        chunk = ""
        for line in text.split("\n"):
            if len(chunk) + len(line) + 1 > 1900:
                blocks.append({
                    "object": "block", "type": "paragraph",
                    "paragraph": {"rich_text": [{"text": {"content": chunk.strip()}}]},
                })
                chunk = ""
            chunk += line + "\n"
        if chunk.strip():
            blocks.append({
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": [{"text": {"content": chunk.strip()}}]},
            })
        return blocks

    def _replace_page_blocks(self, page_id: str, new_blocks: list):
        """Delete all existing blocks on a page, then append new ones."""
        # 1. Fetch existing child blocks
        resp = requests.get(
            f"{NOTION_API_URL}/blocks/{page_id}/children",
            headers=self.headers,
            timeout=30,
        )
        resp.raise_for_status()
        for block in resp.json().get("results", []):
            requests.delete(
                f"{NOTION_API_URL}/blocks/{block['id']}",
                headers=self.headers,
                timeout=15,
            )
        # 2. Append new blocks
        if new_blocks:
            requests.patch(
                f"{NOTION_API_URL}/blocks/{page_id}/children",
                json={"children": new_blocks},
                headers=self.headers,
                timeout=15,
            ).raise_for_status()

    def deduplicate_pages(self) -> int:
        """Find duplicate pages in Notion (by title+date) and archive extras.

        Keeps the page with the most content; archives the rest.
        Returns the number of archived pages.
        """
        # Fetch all pages with their metadata
        pages = []
        start_cursor = None

        while True:
            payload = {"page_size": 100}
            if start_cursor:
                payload["start_cursor"] = start_cursor

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
                # Extract title
                title_text = ""
                for name, prop in props.items():
                    if prop.get("type") == "title":
                        parts = prop.get("title", [])
                        title_text = "".join(p.get("plain_text", "") for p in parts)
                        break
                # Extract date
                date_prop = props.get("Date", {})
                date_str = ""
                if date_prop.get("type") == "date" and date_prop.get("date"):
                    date_str = (date_prop["date"].get("start") or "")[:10]
                # Extract content length as richness indicator
                content_prop = props.get("Content", {})
                content_len = 0
                if content_prop.get("type") == "rich_text":
                    for part in content_prop.get("rich_text", []):
                        content_len += len(part.get("plain_text", ""))

                pages.append({
                    "id": page["id"],
                    "title": title_text,
                    "date": date_str,
                    "content_len": content_len,
                    "key": f"{title_text.lower().strip()}|{date_str}",
                })

            if not data.get("has_more"):
                break
            start_cursor = data.get("next_cursor")

        # Group by title+date key
        groups: Dict[str, list] = {}
        for p in pages:
            groups.setdefault(p["key"], []).append(p)

        archived = 0
        for key, group in groups.items():
            if len(group) <= 1:
                continue
            # Keep the one with most content; archive the rest
            group.sort(key=lambda p: p["content_len"], reverse=True)
            for dup in group[1:]:
                try:
                    requests.patch(
                        f"{NOTION_API_URL}/pages/{dup['id']}",
                        json={"archived": True},
                        headers=self.headers,
                        timeout=15,
                    ).raise_for_status()
                    archived += 1
                    logger.info(f"Archived duplicate: {dup['title']}")
                except Exception as e:
                    logger.error(f"Failed to archive page {dup['id']}: {e}")

        logger.info(f"Dedup complete: archived {archived} duplicate pages")
        return archived

    def refresh_content(self, priorities: Optional[List[str]] = None) -> int:
        """Regenerate content for existing pages using the current prompt.

        Queries pages by priority, regenerates content via OpenAI, and updates
        both the Content property and page body blocks.
        Returns the number of updated pages.
        """
        if priorities is None:
            priorities = ["S", "A"]

        from src.content_generator import _generate_single, CONTENT_PROMPT
        from src.models import Event

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OPENAI_API_KEY not set, cannot refresh content")
            return 0

        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        schema = self._get_db_schema()

        # Query pages matching target priorities
        updated = 0
        for priority in priorities:
            start_cursor = None
            while True:
                payload = {
                    "filter": {"property": "Priority", "select": {"equals": priority}},
                    "page_size": 100,
                }
                if start_cursor:
                    payload["start_cursor"] = start_cursor

                resp = requests.post(
                    f"{NOTION_API_URL}/databases/{self.database_id}/query",
                    json=payload,
                    headers=self.headers,
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()

                for page in data.get("results", []):
                    try:
                        updated += self._refresh_single_page(client, page, schema)
                    except Exception as e:
                        logger.error(f"Failed to refresh page {page['id']}: {e}")

                if not data.get("has_more"):
                    break
                start_cursor = data.get("next_cursor")

        logger.info(f"Refreshed content for {updated} pages")
        return updated

    def _refresh_single_page(self, client, page: dict, schema: dict) -> int:
        """Regenerate content for a single Notion page. Returns 1 if updated, 0 otherwise."""
        from src.content_generator import _generate_single
        from src.models import Event
        import json as _json

        props = page.get("properties", {})
        page_id = page["id"]

        # Extract event data from Notion page properties
        title_text = ""
        for name, prop in props.items():
            if prop.get("type") == "title":
                parts = prop.get("title", [])
                title_text = "".join(p.get("plain_text", "") for p in parts)
                break

        # Skip weekly roundup pages
        source_prop = props.get("Source", {})
        if source_prop.get("type") == "select":
            source_sel = source_prop.get("select") or {}
            if source_sel.get("name") == "weekly-roundup":
                return 0

        date_start = None
        date_prop = props.get("Date", {})
        if date_prop.get("type") == "date" and date_prop.get("date"):
            date_str = date_prop["date"].get("start", "")
            if date_str:
                try:
                    date_start = datetime.fromisoformat(date_str)
                except ValueError:
                    pass

        location = ""
        loc_prop = props.get("Location", {})
        if loc_prop.get("type") == "rich_text":
            location = "".join(p.get("plain_text", "") for p in loc_prop.get("rich_text", []))

        cost = "unknown"
        cost_prop = props.get("Cost", {})
        if cost_prop.get("type") == "rich_text":
            cost = "".join(p.get("plain_text", "") for p in cost_prop.get("rich_text", []))

        url = ""
        url_prop = props.get("URL", {})
        if url_prop.get("type") == "url":
            url = url_prop.get("url") or ""

        source_name = "unknown"
        if source_prop.get("type") == "select" and source_prop.get("select"):
            source_name = source_prop["select"].get("name", "unknown")

        tags = []
        tags_prop = props.get("Tags", {})
        if tags_prop.get("type") == "multi_select":
            tags = [t["name"] for t in tags_prop.get("multi_select", [])]

        # Read page body as description
        description = ""
        try:
            blocks_resp = requests.get(
                f"{NOTION_API_URL}/blocks/{page_id}/children",
                headers=self.headers,
                timeout=15,
            )
            blocks_resp.raise_for_status()
            for block in blocks_resp.json().get("results", []):
                btype = block.get("type", "")
                if btype in ("paragraph", "heading_2", "heading_3"):
                    texts = block.get(btype, {}).get("rich_text", [])
                    description += "".join(t.get("plain_text", "") for t in texts) + "\n"
        except Exception:
            pass

        # Build Event object and regenerate
        event = Event(
            title=title_text,
            date_start=date_start,
            location=location or None,
            description=description.strip() or None,
            source_url=url or "https://unknown",
            source_name=source_name,
            cost=cost or "unknown",
            tags=tags,
        )

        content = _generate_single(client, event)
        headline = content.get("headline", "")
        body_text = content.get("body", "")
        hashtags = content.get("hashtags", [])

        # Update Content property
        prop_updates = {}
        content_preview = f"【{headline}】{body_text}"[:2000] if headline else body_text[:2000]
        if "Content" in schema and content_preview:
            prop_updates["Content"] = {"rich_text": [{"text": {"content": content_preview}}]}

        if prop_updates:
            requests.patch(
                f"{NOTION_API_URL}/pages/{page_id}",
                json={"properties": prop_updates},
                headers=self.headers,
                timeout=15,
            ).raise_for_status()

        # Replace page body blocks: delete old, create new
        try:
            blocks_resp = requests.get(
                f"{NOTION_API_URL}/blocks/{page_id}/children",
                headers=self.headers,
                timeout=15,
            )
            blocks_resp.raise_for_status()
            for block in blocks_resp.json().get("results", []):
                requests.delete(
                    f"{NOTION_API_URL}/blocks/{block['id']}",
                    headers=self.headers,
                    timeout=15,
                )
        except Exception:
            pass

        # Create new blocks
        new_blocks = []
        if headline:
            new_blocks.append({
                "object": "block", "type": "heading_2",
                "heading_2": {"rich_text": [{"text": {"content": headline}}]},
            })
        if body_text:
            new_blocks.append({
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": [{"text": {"content": body_text[:2000]}}]},
            })
        if hashtags:
            new_blocks.append({
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": [{"text": {"content": " ".join(hashtags)}}]},
            })

        if new_blocks:
            requests.patch(
                f"{NOTION_API_URL}/blocks/{page_id}/children",
                json={"children": new_blocks},
                headers=self.headers,
                timeout=15,
            ).raise_for_status()

        logger.info(f"Refreshed content for: {title_text}")
        return 1


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
