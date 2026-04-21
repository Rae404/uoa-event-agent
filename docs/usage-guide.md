# UoA Event Agent 使用教程

## 日常使用

### 一键抓取 + AI 评分（最常用）

```bash
cd /Users/rae/uoa-event-agent
source .venv/bin/activate
python -m src.cli --verbose
```

输出文件在 `output/events_2026-03-23.json`（按当天日期命名）。

### 只想看看有什么活动（不花 API 钱）

```bash
python -m src.cli --no-ai --verbose
```

规则评分仍然会筛选和排序，只是没有 AI 打标签和优先级。

### 只抓特定来源

```bash
# 只抓 UoA 和 Meetup
python -m src.cli --sources uoa meetup --verbose

# 只抓 Eventfinda
python -m src.cli --sources eventfinda --no-ai
```

可选来源：`eventfinda` `eventbrite` `meetup` `uoa` `council`

### 生成小红书/公众号推送文案

```bash
python -m src.cli --generate-content --verbose
```

会为 S 和 A 级活动生成中文文案，输出到 `output/content_2026-03-23.json`。

### 推送到 Notion（需要先配置，见 docs/notion-setup.md）

```bash
python -m src.cli --notion --verbose
```

### 所有参数一览

| 参数 | 作用 |
|------|------|
| `--sources X Y` | 指定数据源 |
| `--no-ai` | 跳过 AI 评分 |
| `--limit N` | 每个源最多抓 N 条（默认 50） |
| `--output PATH` | 自定义输出路径 |
| `--generate-content` | 生成推送文案 |
| `--notion` | 推送到 Notion |
| `--verbose` | 显示详细日志 |

---

## 输出文件说明

`output/events_2026-03-23.json` 里每条活动长这样：

```json
{
  "title": "Build with AI Auckland 2026",
  "date_start": "2026-03-28T09:00:00",
  "location": "101 Pakenham St West, Auckland",
  "cost": "free",
  "source_name": "meetup",
  "score": 70,
  "priority": "A",
  "tags": ["新手友好", "涨知识"],
  "source_url": "https://www.meetup.com/..."
}
```

**优先级含义：**
- **S** — 必推，极高价值
- **A** — 强烈推荐
- **B** — 可以推荐
- **C** — 不推荐（已被过滤）

---

## GitHub Actions 自动化

已配置好每天 NZST 8:00 自动运行。你需要在 GitHub 设置 Secrets：

1. 打开 https://github.com/Rae404/uoa-event-agent/settings/secrets/actions
2. 添加 `OPENAI_API_KEY`（你的 OpenAI key）
3. 可选：添加 `NOTION_TOKEN` 和 `NOTION_DATABASE_ID`

设置好后每天自动抓取 → AI 评分 → 提交结果到 repo。

也可以手动触发：GitHub repo → Actions → Daily Event Scrape → Run workflow

---

## 历史去重

系统会自动记录已处理过的活动（`output/.history.json`）。每天运行只会输出新活动，不会重复。

如果想重置（重新抓取所有活动）：

```bash
rm output/.history.json
```

---

## 常见问题

**Q: 运行报错 `insufficient_quota`**
A: OpenAI 余额不足，去 https://platform.openai.com/settings/organization/billing 充值。加 `--no-ai` 可以先不花钱跑。

**Q: 某个源抓不到数据**
A: 网站可能改了结构。加 `--verbose` 看详细日志，大部分情况是网络问题，重试即可。

**Q: 想换别的 AI 模型 / 提供商**
A: 先改 `config/settings.py` 里的 `AI_MODEL`。如果要切换到别的 SDK，再同步调整 `ai_scorer.py` 和 `content_generator.py` 的客户端实现。

**Q: 输出太多/太少活动**
A: 调整 `config/settings.py` 里的 `SCORE_MINIMUM_THRESHOLD`（当前 15，调高=更严格，调低=更多活动）。
