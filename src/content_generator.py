"""OpenAI-powered Chinese content generation for event pushes."""

import json
import logging
import os
from typing import List, Optional

from openai import OpenAI
from dotenv import load_dotenv

from config.settings import AI_MODEL
from src.models import Event

logger = logging.getLogger(__name__)

load_dotenv()

CONTENT_PROMPT = """\
你是一个面向奥克兰大学(UoA)中国留学生的活动推荐号编辑。
请根据以下活动信息，生成一条适合发在小红书/公众号/朋友圈的中文推送文案。

要求：
1. 标题要吸引眼球，用留学生喜欢的语气（轻松、有梗、接地气）
2. 正文简洁明了，包含：活动亮点、时间、地点、费用
3. 如果是免费活动，重点强调"白嫖"/"薅羊毛"
4. 加上合适的 emoji
5. 结尾加一句互动引导（如"约吗？评论区集合！"）
6. 总字数控制在 150-300 字

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
                "event_date": event.date_start.strftime("%Y-%m-%d %H:%M") if event.date_start else None,
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
    date_str = event.date_start.strftime("%Y年%m月%d日 %H:%M") if event.date_start else "时间待定"
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
                "content": "你是一个活动推送文案编辑，只返回JSON格式的文案。",
            },
            {
                "role": "user",
                "content": CONTENT_PROMPT + "\n活动信息：\n" + event_info,
            },
        ],
    )

    response_text = response.choices[0].message.content.strip()
    return json.loads(response_text)
