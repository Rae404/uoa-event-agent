"""OpenAI-powered Chinese content generation for event pushes."""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from openai import OpenAI
from dotenv import load_dotenv

from config.settings import AI_MODEL
from src.models import Event

NZST = timezone(timedelta(hours=12))
NZ_TZ = ZoneInfo("Pacific/Auckland")

logger = logging.getLogger(__name__)


def _to_nz(dt: datetime) -> datetime:
    """Convert a datetime to Pacific/Auckland, handling naive and mixed offsets.

    - Naive datetime: treat as NZ local, attach NZ tzinfo
    - +12:00 / +13:00 (NZ-ish): hour is already local, just normalise offset
    - +00:00 or other: proper timezone conversion via astimezone
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=NZ_TZ)
    offset_hours = dt.utcoffset().total_seconds() / 3600
    if 11 <= offset_hours <= 14:
        # Already NZ local time, just fix the offset for DST correctness
        return dt.replace(tzinfo=NZ_TZ)
    return dt.astimezone(NZ_TZ)

load_dotenv()

CONTENT_PROMPT = """\
你是一个在奥克兰大学(UoA)读书的中国留学生，运营一个活动分享账号。
请根据以下活动信息，用个人分享的口吻写一条小红书风格的中文推送。

写作要求：
1. 标题简洁吸引人，15字以内，包含活动关键词
2. 正文用第一人称，像跟朋友分享一样自然，包含：活动亮点、时间、地点、费用
3. 根据活动类型调整语气：
   - 知识/技术/讲座类：突出"涨知识""干货"，语气专业但亲切
   - 娱乐/社交/美食类：语气轻松活泼
   - 运动/户外类：突出体验感
4. 免费活动可以提到"0元参加"或"不花钱"，但不要用"白嫖""薅羊毛""免费领"
5. 适当使用 emoji 做段落分隔，不要过多
6. 结尾自然收束，可以加互动引导但不要用"求点赞""求收藏"
7. 总字数 150-300 字

⚠️ 小红书合规（必须遵守）：
- 禁止极限词：最好、最强、顶级、极致、独一无二
- 禁止引流词：微信、加我、私聊、看主页
- 禁止诱导互动：点赞、收藏、关注、转发
- 不要提及其他平台名称（淘宝、抖音、B站等）
- 不要编造活动内容，严格基于提供的信息

⚠️ 事实准确性：
- 奥克兰大学简称"奥大"或直接用"UoA"，绝对不要写"乌大"
- 活动名称、时间、地点必须与原始信息一致，不要自行编造

请返回 JSON 格式：
{
  "headline": "推送标题（15字以内）",
  "body": "正文内容",
  "hashtags": ["#标签1", "#标签2"]
}
"""


def generate_content(events: List[Event], priorities: Optional[List[str]] = None) -> List[dict]:
    """Generate Chinese push content for high-priority events.

    Args:
        events: List of scored events
        priorities: Which priorities to generate for (default: S and A)

    Returns:
        List of dicts with event info + generated content
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set, skipping content generation")
        return []

    if priorities is None:
        priorities = ["S", "A"]

    # Filter to target priorities
    target_events = [e for e in events if e.priority in priorities]
    if not target_events:
        logger.info(f"No events with priority {priorities} found, skipping content generation")
        return []

    logger.info(f"Generating content for {len(target_events)} events (priorities: {priorities})")

    client = OpenAI(api_key=api_key)
    results = []

    for event in target_events:
        try:
            content = _generate_single(client, event)
            results.append({
                "event_title": event.title,
                "event_url": event.source_url,
                "event_date": _to_nz(event.date_start).strftime("%Y-%m-%d %H:%M") if event.date_start else None,
                "event_score": event.score,
                "event_priority": event.priority,
                "generated": content,
            })
        except Exception as e:
            logger.error(f"Content generation failed for '{event.title}': {e}")

    logger.info(f"Generated content for {len(results)}/{len(target_events)} events")
    return results


def _generate_single(client: OpenAI, event: Event) -> dict:
    """Generate content for a single event."""
    date_str = _to_nz(event.date_start).strftime("%Y年%m月%d日 %H:%M") if event.date_start else "时间待定"
    tags_str = ", ".join(event.tags) if event.tags else "无"

    event_info = f"""
标题: {event.title}
日期: {date_str}
地点: {event.location or '待定'}
费用: {event.cost}
来源: {event.source_name}
标签: {tags_str}
描述: {(event.description or '无')[:800]}
链接: {event.source_url}
"""

    response = client.chat.completions.create(
        model=AI_MODEL,
        max_tokens=1024,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": "你是一个在新西兰留学的中国学生，用个人分享口吻写活动推荐，只返回JSON格式。",
            },
            {
                "role": "user",
                "content": CONTENT_PROMPT + "\n活动信息：\n" + event_info,
            },
        ],
    )

    response_text = response.choices[0].message.content.strip()
    return json.loads(response_text)


def generate_brief_summary(event: Event) -> dict:
    """Generate a template-based brief summary for B-level events. No API call."""
    date_str = _to_nz(event.date_start).strftime("%m/%d %a") if event.date_start else "日期待定"
    location = event.location or "地点待定"
    cost = "免费" if event.cost.lower() in ("free", "免费", "$0", "0") else event.cost

    body = f"📅 {date_str} | 📍 {location} | 💰 {cost}"
    return {
        "headline": event.title,
        "body": body,
        "hashtags": [],
    }


def generate_brief_summaries(events: List[Event]) -> Dict[str, dict]:
    """Generate template-based summaries for non-S/A events that have dates.

    Covers B-level + unscored events (priority is None in --no-ai mode).
    """
    target = [e for e in events if e.priority not in ("S", "A") and e.date_start]
    if not target:
        return {}

    content_map = {}
    for event in target:
        content_map[event.title] = generate_brief_summary(event)

    logger.info(f"Generated brief summaries for {len(content_map)} non-S/A events (template, no API)")
    return content_map


WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


# --- Supermarket deals summary templates ---

def generate_deals_summary(promos: list) -> Optional[str]:
    """Generate a weekly deals summary post from supermarket promos.

    Args:
        promos: List of SupermarketPromo objects from all stores.

    Returns:
        Formatted summary text for Xiaohongshu post, or None if no promos.
    """
    if not promos:
        return None

    from src.models import SupermarketPromo
    from src.notifier import classify_promo, STORE_EMOJI, STORE_NAME_CN

    now = datetime.now(tz=NZ_TZ)
    # Calculate this week's date range (Mon-Sun)
    week_start = now - timedelta(days=now.weekday())
    week_end = week_start + timedelta(days=6)
    date_range = f"{week_start.strftime('%-m/%-d')}-{week_end.strftime('%-m/%-d')}"

    # Group promos by store
    store_order = ["woolworths", "paknsave", "newworld"]
    grouped: Dict[str, list] = {}
    for p in promos:
        grouped.setdefault(p.store, []).append(p)

    lines = [f"【三大超市本周特价速报 {date_range}】", ""]

    for store in store_order:
        store_promos = grouped.get(store, [])
        if not store_promos:
            continue

        emoji = STORE_EMOJI.get(store, "🔵")
        store_cn = STORE_NAME_CN.get(store, store)
        lines.append(f"{emoji} {store_cn}")

        for p in store_promos:
            level = classify_promo(p.title)
            # Add description (product count etc.)
            desc = p.description or ""
            if p.title == "本周特价" and desc:
                lines.append(f"共{desc}件商品特价" if desc.isdigit() else desc)
            elif level == "URGENT":
                lines.append(f"⚡ {p.title}！")
            elif level == "STANDARD":
                lines.append(f"🔥 {p.title}！")
            else:
                lines.append(f"{p.title}")

        lines.append("")

    lines.append("👉 各超市特价链接见评论区")
    lines.append("#奥克兰留学 #新西兰超市 #省钱攻略 #NZ留学生")

    return "\n".join(lines).strip()


def generate_weekly_roundup(events: List[Event], mode: str = "full") -> Optional[str]:
    """Generate a weekly roundup combining events for the current week.

    Args:
        events: All scored events.
        mode: "full" = Mon–Sun (complete roundup), "supplement" = Thu–Sun only.

    Returns the formatted roundup text, or None if no events.
    """
    now = datetime.now(tz=NZ_TZ)
    # Monday of this week
    week_start = now - timedelta(days=now.weekday())
    week_end = week_start + timedelta(days=6)

    week_start_date = week_start.date()
    week_end_date = week_end.date()

    if mode == "supplement":
        # Supplement covers Thursday (weekday 3) through Sunday
        filter_start = (week_start + timedelta(days=3)).date()
    else:
        filter_start = week_start_date

    # Filter events in the target range (normalize all dates to NZ time)
    week_events = []
    for e in events:
        if not e.date_start:
            continue
        d = _to_nz(e.date_start)
        edate = d.date()
        if filter_start <= edate <= week_end_date:
            week_events.append(e)

    if not week_events:
        logger.info(f"No events for roundup (mode={mode})")
        return None

    # Sort by date (normalize to aware datetime for comparison)
    def _sort_key(e):
        return _to_nz(e.date_start)
    week_events.sort(key=_sort_key)

    # Group by priority
    s_events = [e for e in week_events if e.priority == "S"]
    a_events = [e for e in week_events if e.priority == "A"]
    b_events = [e for e in week_events if e.priority in ("B", None)]

    if mode == "supplement":
        sup_start = week_start + timedelta(days=3)
        date_range = f"{sup_start.strftime('%-m.%-d')}-{week_end.strftime('%-m.%-d')}"
        lines = [f"【周中补充 | {date_range}】", ""]
    else:
        date_range = f"{week_start.strftime('%-m.%-d')}-{week_end.strftime('%-m.%-d')}"
        lines = [f"【本周活动情报 | {date_range}】", ""]

    idx = 1

    def _extract_headline(text: str) -> str:
        """Extract Chinese headline from Content field (e.g. '【headline】body...')."""
        if not text:
            return ""
        # Try to extract 【...】 pattern
        import re
        m = re.search(r"【(.+?)】", text)
        if m:
            return m.group(1).strip()
        # Fallback: first line, truncated
        return text.strip().split("\n")[0][:40]

    def _format_time_cn(d: datetime) -> str:
        """Format time in Chinese style: 下午5:00, 上午10:30."""
        hour = d.hour
        minute = d.minute
        if hour < 12:
            period = "上午"
            h12 = hour if hour != 0 else 12
        else:
            period = "下午"
            h12 = hour - 12 if hour != 12 else 12
        return f"{period}{h12}:{minute:02d}"

    def _format_event(e: Event, num: int) -> str:
        d = _to_nz(e.date_start) if e.date_start else None
        if d:
            base = f"{d.strftime('%-m/%-d')} {WEEKDAY_CN[d.weekday()]}"
            # Skip time display for midnight-ish (date-only events stored as 00:00)
            if d.hour in (0, 1) and d.minute == 0:
                date_str = base
            else:
                date_str = f"{base} {_format_time_cn(d)}"
        else:
            date_str = "待定"
        loc = e.location.split(",")[0].strip() if e.location else ""
        cost = "免费" if e.cost.lower() in ("free", "免费", "$0", "0") else e.cost
        line = f"{num}. {e.title} - {date_str}"
        if loc:
            line += f" @ {loc}"
        line += f" | {cost}"
        # Add Chinese headline from generated content
        headline = _extract_headline(e.description) if e.description else ""
        if headline:
            line += f"\n   → {headline}"
        return line

    if s_events:
        lines.append("🔥 必去：")
        for e in s_events:
            lines.append(_format_event(e, idx))
            idx += 1
        lines.append("")

    if a_events:
        lines.append("⭐ 推荐：")
        for e in a_events:
            lines.append(_format_event(e, idx))
            idx += 1
        lines.append("")

    if b_events:
        lines.append("📋 其他可以看看：")
        for e in b_events:
            lines.append(_format_event(e, idx))
            idx += 1
        lines.append("")

    roundup_text = "\n".join(lines).strip()
    logger.info(f"Generated weekly roundup ({mode}) with {len(week_events)} events")
    return roundup_text
