"""Telegram notification for new supermarket promos."""

import logging
import os
from typing import List

import requests

from src.models import SupermarketPromo

logger = logging.getLogger(__name__)

STORE_EMOJI = {
    "woolworths": "🟢",
    "paknsave": "🟡",
    "newworld": "🔴",
}

STORE_NAME_CN = {
    "woolworths": "绿超 Woolworths",
    "paknsave": "黄超 PAK'nSAVE",
    "newworld": "红超 New World",
}

# Keywords that indicate urgency level
URGENT_KEYWORDS = {"flash", "3 day", "three day", "one day", "1 day", "limited", "clearance", "半价", "half price"}
SEASONAL_KEYWORDS = {"easter", "christmas", "boxing day", "black friday", "new year", "anzac", "matariki"}


def classify_promo(title: str) -> str:
    """Classify a promo as URGENT, STANDARD, or ROUTINE."""
    lower = title.lower()
    for kw in URGENT_KEYWORDS:
        if kw in lower:
            return "URGENT"
    for kw in SEASONAL_KEYWORDS:
        if kw in lower:
            return "STANDARD"
    return "ROUTINE"


def send_telegram(message: str) -> bool:
    """Send a message via Telegram Bot API. Returns True on success."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        logger.info("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID), skipping")
        return False

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("Telegram notification sent")
        return True
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


def notify_new_promos(new_promos: List[SupermarketPromo]):
    """Send Telegram notifications for new promos based on urgency.

    - URGENT promos: immediate individual notification
    - STANDARD promos: batched into one message
    - ROUTINE promos: no notification (logged only)
    """
    if not new_promos:
        return

    urgent = []
    standard = []

    for p in new_promos:
        level = classify_promo(p.title)
        if level == "URGENT":
            urgent.append(p)
        elif level == "STANDARD":
            standard.append(p)
        # ROUTINE: skip notification

    # Send URGENT promos immediately
    for p in urgent:
        emoji = STORE_EMOJI.get(p.store, "🔵")
        store_cn = STORE_NAME_CN.get(p.store, p.store)
        dates = ""
        if p.date_start and p.date_end:
            dates = f"\n📅 {p.date_start.strftime('%m/%d')}-{p.date_end.strftime('%m/%d')}"
        msg = (
            f"🔥 <b>紧急促销活动！</b>\n"
            f"{emoji} {store_cn}\n"
            f"<b>{p.title}</b>{dates}\n"
            f"🔗 {p.url}\n"
            f"\n📸 记得截图发小红书！"
        )
        send_telegram(msg)

    # Batch STANDARD promos into one message
    if standard:
        lines = ["🛒 <b>新促销活动</b>\n"]
        for p in standard:
            emoji = STORE_EMOJI.get(p.store, "🔵")
            store_cn = STORE_NAME_CN.get(p.store, p.store)
            lines.append(f"{emoji} {store_cn}: {p.title}")
        lines.append("\n📸 记得截图发小红书！")
        send_telegram("\n".join(lines))

    # Log summary
    logger.info(
        f"Notification summary: {len(urgent)} urgent, {len(standard)} standard, "
        f"{len(new_promos) - len(urgent) - len(standard)} routine (skipped)"
    )
