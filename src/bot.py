"""Telegram bot command handler — process /deals, /summary, /screenshot, /status."""

import json
import logging
import os
from datetime import datetime
from typing import Optional

import requests
from dotenv import load_dotenv

from config.settings import DEFAULT_OUTPUT_DIR

load_dotenv()

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
OFFSET_FILE = os.path.join(DEFAULT_OUTPUT_DIR, ".bot_offset.json")


def _send(text: str, chat_id: Optional[str] = None, parse_mode: str = "HTML"):
    """Send a text message."""
    cid = chat_id or CHAT_ID
    resp = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": cid, "text": text, "parse_mode": parse_mode},
        timeout=10,
    )
    resp.raise_for_status()


def _send_photo(photo_path: str, caption: str = "", chat_id: Optional[str] = None):
    """Send a photo file."""
    cid = chat_id or CHAT_ID
    with open(photo_path, "rb") as f:
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
            data={"chat_id": cid, "caption": caption},
            files={"photo": f},
            timeout=30,
        )
    resp.raise_for_status()


def _load_offset() -> int:
    """Load last processed update_id."""
    if not os.path.exists(OFFSET_FILE):
        return 0
    try:
        with open(OFFSET_FILE, "r") as f:
            return json.load(f).get("offset", 0)
    except (json.JSONDecodeError, IOError):
        return 0


def _save_offset(offset: int):
    """Save last processed update_id."""
    os.makedirs(os.path.dirname(OFFSET_FILE) or ".", exist_ok=True)
    with open(OFFSET_FILE, "w") as f:
        json.dump({"offset": offset}, f)


# --- Command handlers ---

def cmd_deals(chat_id: str):
    """Run Woolworths quick scan and return results."""
    _send("🔍 正在扫描超市活动...", chat_id)

    try:
        from src.scrapers.woolworths import WoolworthsScraper
        scraper = WoolworthsScraper()
        promos = scraper.run_promos()

        if not promos:
            _send("暂无检测到活动", chat_id)
            return

        lines = ["🛒 <b>当前超市活动</b>\n"]
        for p in promos:
            dates = ""
            if p.date_start and p.date_end:
                dates = f" ({p.date_start.strftime('%m/%d')}-{p.date_end.strftime('%m/%d')})"
            desc = f"\n   📦 {p.description}" if p.description else ""
            lines.append(f"🟢 <b>{p.title}</b>{dates}{desc}")

        # Try PAK'nSAVE and New World if Playwright available
        for store_name, scraper_cls_name in [("paknsave", "PaknsaveScraper"), ("newworld", "NewWorldScraper")]:
            try:
                mod = __import__(f"src.scrapers.{store_name}", fromlist=[scraper_cls_name])
                scraper_cls = getattr(mod, scraper_cls_name)
                store_promos = scraper_cls().run_promos()
                emoji = "🟡" if store_name == "paknsave" else "🔴"
                for p in store_promos:
                    dates = ""
                    if p.date_start and p.date_end:
                        dates = f" ({p.date_start.strftime('%m/%d')}-{p.date_end.strftime('%m/%d')})"
                    lines.append(f"{emoji} <b>{p.title}</b>{dates}")
            except Exception:
                pass

        _send("\n".join(lines), chat_id)

    except Exception as e:
        _send(f"扫描失败: {e}", chat_id)


def cmd_summary(chat_id: str):
    """Generate this week's deals summary for Xiaohongshu."""
    _send("📝 正在生成本周速报...", chat_id)

    try:
        from src.scrapers.woolworths import WoolworthsScraper
        from src.content_generator import generate_deals_summary

        # Scrape current promos
        all_promos = []
        all_promos.extend(WoolworthsScraper().run_promos())

        for store_name, scraper_cls_name in [("paknsave", "PaknsaveScraper"), ("newworld", "NewWorldScraper")]:
            try:
                mod = __import__(f"src.scrapers.{store_name}", fromlist=[scraper_cls_name])
                scraper_cls = getattr(mod, scraper_cls_name)
                all_promos.extend(scraper_cls().run_promos())
            except Exception:
                pass

        summary = generate_deals_summary(all_promos)
        if summary:
            _send(f"<pre>{summary}</pre>\n\n👆 复制到小红书发布", chat_id)
        else:
            _send("暂无活动数据，无法生成速报", chat_id)

    except Exception as e:
        _send(f"生成失败: {e}", chat_id)


def cmd_screenshot(chat_id: str):
    """Take and send screenshots of supermarket pages."""
    _send("📸 正在截图超市页面...", chat_id)

    try:
        from src.screenshot import take_all_screenshots
        paths = take_all_screenshots()

        if not paths:
            _send("截图失败，可能 Playwright 未安装", chat_id)
            return

        for path in paths:
            store = os.path.basename(path).split("_")[0]
            store_names = {"woolworths": "绿超", "paknsave": "黄超", "newworld": "红超"}
            caption = store_names.get(store, store)
            try:
                _send_photo(path, caption, chat_id)
            except Exception as e:
                _send(f"发送 {caption} 截图失败: {e}", chat_id)

    except Exception as e:
        _send(f"截图失败: {e}", chat_id)


def cmd_status(chat_id: str):
    """Report system status — last scan time, new promos count."""
    lines = ["📊 <b>系统状态</b>\n"]

    # Check promo history
    from src.promo_pipeline import PROMO_HISTORY_FILE
    if os.path.exists(PROMO_HISTORY_FILE):
        mtime = os.path.getmtime(PROMO_HISTORY_FILE)
        last_scan = datetime.fromtimestamp(mtime).strftime("%m/%d %H:%M")
        try:
            with open(PROMO_HISTORY_FILE, "r") as f:
                data = json.load(f)
            total = len(data.get("seen", []))
            lines.append(f"🕐 上次扫描: {last_scan}")
            lines.append(f"📋 已追踪活动: {total} 个")
        except Exception:
            lines.append(f"🕐 上次扫描: {last_scan}")
    else:
        lines.append("🕐 尚未运行过扫描")

    # Check latest promos file
    today = datetime.now().strftime("%Y-%m-%d")
    promos_file = os.path.join(DEFAULT_OUTPUT_DIR, f"promos_{today}.json")
    if os.path.exists(promos_file):
        try:
            with open(promos_file, "r") as f:
                promos = json.load(f)
            lines.append(f"📦 今日活动: {len(promos)} 个")
        except Exception:
            pass

    # Check latest events file
    events_file = os.path.join(DEFAULT_OUTPUT_DIR, f"events_{today}.json")
    if os.path.exists(events_file):
        try:
            with open(events_file, "r") as f:
                events = json.load(f)
            lines.append(f"🎪 今日活动(events): {len(events)} 个")
        except Exception:
            pass

    # Check event history
    history_file = os.path.join(DEFAULT_OUTPUT_DIR, ".history.json")
    if os.path.exists(history_file):
        try:
            with open(history_file, "r") as f:
                data = json.load(f)
            lines.append(f"📚 累计追踪活动: {len(data.get('seen', []))} 个")
        except Exception:
            pass

    lines.append(f"\n⏰ 查询时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    _send("\n".join(lines), chat_id)


COMMANDS = {
    "/deals": cmd_deals,
    "/summary": cmd_summary,
    "/screenshot": cmd_screenshot,
    "/status": cmd_status,
}


def poll_and_handle():
    """Poll for new messages and handle commands. Called by GitHub Actions."""
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        return

    offset = _load_offset()
    params = {"timeout": 5}
    if offset:
        params["offset"] = offset + 1

    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        updates = resp.json().get("result", [])
    except Exception as e:
        logger.error(f"Failed to get updates: {e}")
        return

    if not updates:
        logger.info("No new messages")
        return

    for update in updates:
        update_id = update.get("update_id", 0)
        message = update.get("message", {})
        text = message.get("text", "").strip()
        chat_id = str(message.get("chat", {}).get("id", ""))

        # Only respond to the configured chat (security)
        if chat_id != CHAT_ID:
            logger.warning(f"Ignoring message from unknown chat: {chat_id}")
            _save_offset(update_id)
            continue

        # Match command
        cmd = text.split()[0].lower() if text else ""
        if cmd in COMMANDS:
            logger.info(f"Handling command: {cmd}")
            try:
                COMMANDS[cmd](chat_id)
            except Exception as e:
                logger.error(f"Command {cmd} failed: {e}", exc_info=True)
                _send(f"命令执行失败: {e}", chat_id)
        elif text.startswith("/"):
            _send(
                "🤖 <b>可用命令</b>\n\n"
                "/deals — 查看当前超市活动\n"
                "/summary — 生成本周特价速报\n"
                "/screenshot — 截图超市页面\n"
                "/status — 查看系统状态",
                chat_id,
            )

        _save_offset(update_id)

    logger.info(f"Processed {len(updates)} updates")
