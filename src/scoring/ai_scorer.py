"""OpenAI-based intelligent scoring and tagging."""

import json
import logging
import os
from typing import List

from openai import OpenAI
from dotenv import load_dotenv

from config.settings import AI_BATCH_SIZE, AI_MAX_TOKENS, AI_MODEL, AVAILABLE_TAGS
from src.models import Event

logger = logging.getLogger(__name__)

load_dotenv()

SCORING_PROMPT = """\
你是一个活动推荐助手，服务于奥克兰大学(UoA)的中国留学生群体。

请对以下活动进行评分和打标签。每个活动按 7 个维度评分(每维度 1-10 分)：
1. 用户相关性 — 对 UoA 中国留学生群体的吸引力
2. 参与门槛 — 越低越好（免费、无需报名、语言友好）
3. 收益价值 — 社交、求职、技能、见识、免费福利
4. 来源可信度 — 活动来源的可靠性
5. 信息完整度 — 标题、时间、地点、描述是否齐全
6. 时效性 — 是否即将发生
7. 表现力 — 是否容易做成有吸引力的推送内容

请返回一个 JSON 数组，每个元素格式如下：
{
  "index": 0,
  "score": 75,
  "priority": "A",
  "tags": ["新手友好", "0元参加"],
  "reason": "一句话说明推荐理由"
}

优先级规则：
- S (90-100): 必推，对目标用户极有价值
- A (70-89): 强烈推荐
- B (50-69): 可以推荐
- C (0-49): 不推荐

可用标签：
""" + ", ".join(AVAILABLE_TAGS) + """

活动列表：
"""


def ai_score_events(events: List[Event]) -> List[Event]:
    """Score events using OpenAI API in batches."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set, skipping AI scoring")
        return events

    client = OpenAI(api_key=api_key)
    scored_events = []

    for i in range(0, len(events), AI_BATCH_SIZE):
        batch = events[i : i + AI_BATCH_SIZE]
        try:
            results = _score_batch(client, batch)
            for result in results:
                idx = result.get("index", 0)
                if 0 <= idx < len(batch):
                    event = batch[idx]
                    event.score = result.get("score", event.score)
                    event.priority = result.get("priority", "C")
                    event.tags = result.get("tags", [])
                    scored_events.append(event)
            # Add any events that weren't in results
            scored_indices = {r.get("index") for r in results}
            for idx, event in enumerate(batch):
                if idx not in scored_indices:
                    scored_events.append(event)
        except Exception as e:
            logger.error(f"AI scoring batch failed: {e}")
            scored_events.extend(batch)  # keep events even if scoring fails

    logger.info(f"AI scored {len(scored_events)} events")
    return scored_events


def _score_batch(client: OpenAI, batch: List[Event]) -> list:
    """Send a batch of events to OpenAI for scoring."""
    events_text = ""
    for i, event in enumerate(batch):
        date_str = event.date_start.strftime("%Y-%m-%d %H:%M") if event.date_start else "未知"
        events_text += f"""
--- 活动 {i} ---
标题: {event.title}
日期: {date_str}
地点: {event.location or '未知'}
费用: {event.cost}
来源: {event.source_name}
描述: {(event.description or '无')[:500]}
链接: {event.source_url}
"""

    response = client.chat.completions.create(
        model=AI_MODEL,
        max_tokens=AI_MAX_TOKENS,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": "你是一个活动评分助手，只返回JSON格式的评分结果。",
            },
            {
                "role": "user",
                "content": SCORING_PROMPT + events_text + "\n\n请返回JSON对象，格式为 {\"results\": [...]}：",
            },
        ],
    )

    response_text = response.choices[0].message.content.strip()
    data = json.loads(response_text)

    # Handle both {"results": [...]} and direct array
    if isinstance(data, dict):
        return data.get("results", data.get("events", []))
    return data
