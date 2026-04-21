# UoA Event Agent

**面向奥克兰大学中国留学生的活动情报聚合工具**  
**A bilingual Auckland event intelligence tool for UoA Chinese students**

UoA Event Agent 会从多个奥克兰本地活动源抓取活动信息，先做规则筛选，再用 AI 进行补充评分与标签整理，最终输出更适合中国留学生阅读和分发的活动结果。  
UoA Event Agent collects events from multiple Auckland sources, filters them with rules, enriches them with AI scoring and tags, and produces cleaner event recommendations for UoA Chinese students.

## Features / 功能

- **Multi-source event scraping** / 多源活动抓取  
  Aggregates Eventfinda, Eventbrite, Meetup, UoA Unievents, and Auckland Council.

- **Two-stage scoring** / 双阶段评分  
  Rule-based pre-filtering first, then optional AI scoring for deeper prioritisation.

- **Chinese tagging** / 中文标签整理  
  Adds practical tags such as `新手友好`、`免费薅羊毛`、`练英语`、`找工有帮助`.

- **Content generation** / 中文文案生成  
  Generates Chinese copy for high-priority events that are ready to post or share.

- **Notion sync** / Notion 同步  
  Pushes selected events into a Notion database for tracking and publishing workflows.

- **Scheduled automation** / 自动化运行  
  Works well with GitHub Actions for daily collection and publishing pipelines.

## Data Sources / 数据源

| Source | Method | Notes |
|------|------|------|
| UoA Unievents | REST API | Official university events feed |
| Eventfinda | HTML + detail pages | Broad Auckland event coverage |
| Eventbrite | `__SERVER_DATA__` JSON parsing | Good for workshops and community events |
| Meetup | JSON-LD | Useful for niche groups and recurring meetups |
| Auckland Council | HTML + detail pages | Public and civic events |

## Project Structure / 项目结构

```text
src/
├── cli.py                  # CLI entry point
├── pipeline.py             # scrape -> dedup -> score -> export
├── exporter.py             # JSON export helpers
├── content_generator.py    # AI-generated Chinese copy
├── notion_sync.py          # Event sync to Notion
├── models.py               # Pydantic models
├── scrapers/
│   ├── eventfinda.py
│   ├── eventbrite.py
│   ├── meetup.py
│   ├── uoa.py
│   └── auckland_council.py
└── scoring/
    ├── scorer.py           # Rule-based scoring
    └── ai_scorer.py        # OpenAI-based scoring
```

## Quick Start / 快速开始

```bash
git clone https://github.com/Rae404/uoa-event-agent.git
cd uoa-event-agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env and add your OPENAI_API_KEY if you want AI scoring/content generation
```

### Run Examples / 常用运行方式

```bash
# Rule-based only, all event sources
python -m src.cli --no-ai --verbose

# Use AI scoring
python -m src.cli --verbose

# Specific sources only
python -m src.cli --sources eventfinda uoa --no-ai

# Generate Chinese content for high-priority events
python -m src.cli --generate-content --verbose

# Push to Notion
python -m src.cli --notion --verbose

# Generate weekly roundup for Notion
python -m src.cli --weekly-roundup full --verbose
```

## CLI Options / 命令行参数

| Flag | English | 中文 |
|------|------|------|
| `--sources` | Event sources to scrape | 指定要抓取的活动源 |
| `--no-ai` | Skip AI scoring | 跳过 AI 评分，仅使用规则评分 |
| `--limit N` | Max events per source | 每个来源最多抓取数量 |
| `--output PATH` | Custom JSON output path | 自定义输出文件路径 |
| `--generate-content` | Generate Chinese post copy | 生成中文发布文案 |
| `--notion` | Push events to Notion | 同步活动到 Notion |
| `--weekly-roundup` | Generate weekly roundup in Notion | 在 Notion 生成周报 |
| `--notion-cleanup` | Deduplicate and refresh Notion content | 清理并刷新 Notion 内容 |
| `--fix-expired` | Rename legacy expired pages | 修复历史过期页面状态 |
| `--verbose` | Enable debug logging | 输出详细日志 |

## Scoring Logic / 评分逻辑

### Layer 1: Rule-Based / 第一层：规则评分

- Free events get a boost / 免费活动优先
- Near UoA campus scores higher / 靠近 UoA 校区的活动优先
- Relevant keywords improve the score / 匹配关键词会加分
- Trusted sources receive extra weight / 官方或高可信来源权重更高
- Very low-score events are filtered before AI / 低分活动会在进入 AI 前被过滤

### Layer 2: AI Scoring / 第二层：AI 评分

- Evaluates relevance, accessibility, value, credibility, completeness, timeliness, and content potential
- Produces priority levels such as `S`, `A`, `B`, `C`
- Assigns reusable Chinese tags for downstream publishing

## Environment Variables / 环境变量

```bash
OPENAI_API_KEY=sk-...

# Optional Notion integration
NOTION_TOKEN=secret_xxxxx
NOTION_DATABASE_ID=xxxxx
```

If `OPENAI_API_KEY` is missing, the project can still run in rule-based mode with `--no-ai`.  
如果没有配置 `OPENAI_API_KEY`，仍可通过 `--no-ai` 使用纯规则模式运行。

## Output / 输出结果

- `output/events_YYYY-MM-DD.json` or similar event export files / 活动抓取结果
- Notion pages for selected events / 同步后的 Notion 页面
- Generated Chinese content for high-priority events / 高优先级活动的中文文案

## Tech Stack / 技术栈

- Python 3.9+
- Requests + BeautifulSoup + lxml
- Pydantic v2
- OpenAI API
- Notion API
- GitHub Actions

## License

MIT
