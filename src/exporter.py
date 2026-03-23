"""Export events to JSON."""

import json
import logging
import os
from datetime import datetime
from typing import List, Optional

from config.settings import DEFAULT_OUTPUT_DIR
from src.models import Event

logger = logging.getLogger(__name__)


def export_content(content: list, output_path: Optional[str] = None) -> str:
    """Export generated content to a JSON file."""
    if not output_path:
        os.makedirs(DEFAULT_OUTPUT_DIR, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        output_path = os.path.join(DEFAULT_OUTPUT_DIR, f"content_{today}.json")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(content, f, ensure_ascii=False, indent=2, default=str)

    logger.info(f"Exported {len(content)} content pieces to {output_path}")
    return output_path


def export_events(events: List[Event], output_path: Optional[str] = None) -> str:
    """Export events to a JSON file. Returns the output path."""
    if not output_path:
        os.makedirs(DEFAULT_OUTPUT_DIR, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        output_path = os.path.join(DEFAULT_OUTPUT_DIR, f"events_{today}.json")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    data = [event.model_dump(mode="json") for event in events]
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    logger.info(f"Exported {len(events)} events to {output_path}")
    return output_path
