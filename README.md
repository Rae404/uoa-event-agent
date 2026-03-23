# UoA Event Agent

**奥克兰大学中国留学生活动情报 Agent** | Auckland events aggregator for UoA Chinese students

自动从多个数据源抓取奥克兰地区活动信息，通过 AI 智能评分和筛选，输出适合中国留学生群体的高质量活动推荐。

---

## Features / 功能

- **Multi-source scraping** — Eventfinda, Eventbrite, Meetup, UoA Unievents, Auckland Council
- **Two-layer scoring** — Rule-based pre-filtering + Claude AI intelligent scoring
- **Chinese tags** — Auto-assigns tags like 新手友好, 免费薅羊毛, 练英语, 找工有帮助
- **Content generation** — AI-generated Chinese push copy for social media
- **Daily automation** — GitHub Actions scheduled scraping

## Architecture / 架构

```
src/
├── cli.py                 # CLI entry point
├── pipeline.py            # Orchestration: scrape → dedup → score → export
├── models.py              # Pydantic Event data model
├── exporter.py            # JSON export
├── content_generator.py   # AI Chinese content generation
├── scrapers/
│   ├── base.py            # Abstract base: rate limiting, retries, sessions
│   ├── eventfinda.py      # HTML cards + detail pages
│   ├── eventbrite.py      # __SERVER_DATA__ JSON parsing
│   ├── meetup.py           # JSON-LD structured data
│   ├── uoa.py             # Direct REST API (no browser needed)
│   └── auckland_council.py # HTML + detail pages
└── scoring/
    ├── scorer.py           # Rule-based scoring (free, fast)
    └── ai_scorer.py        # Claude API batch scoring + tagging
```

## Quick Start / 快速开始

```bash
# Clone and setup
git clone https://github.com/Rae404/uoa-event-agent.git
cd uoa-event-agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Set up API key for AI scoring (optional)
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY

# Run — all sources, rule-based scoring only
python -m src.cli --no-ai --verbose

# Run — with AI scoring
python -m src.cli --verbose

# Run — specific sources
python -m src.cli --sources eventfinda uoa --no-ai

# Run — with content generation
python -m src.cli --generate-content --verbose
```

## CLI Options / 命令行参数

| Flag | Description |
|------|-------------|
| `--sources` | Data sources to scrape (eventfinda, eventbrite, meetup, uoa, council) |
| `--no-ai` | Skip AI scoring, use rule-based only |
| `--limit N` | Max events per source (default: 50) |
| `--output PATH` | Output JSON file path |
| `--generate-content` | Generate Chinese push content for S/A events |
| `--verbose` | Enable debug logging |

## Scoring / 评分机制

### Layer 1: Rule-based (free, instant)
- Free event: +15
- Near UoA campus: +10
- Keyword match (networking, career, workshop...): +5 each
- Source trust: UoA +15, Council +10, Eventfinda/Eventbrite +8, Meetup +5
- Within 7 days: +10
- Below threshold (15): filtered out before AI

### Layer 2: Claude AI (requires API key)
- 7-dimension scoring: relevance, accessibility, value, credibility, completeness, timeliness, content potential
- Priority assignment: S (must-push) → A (recommended) → B (optional) → C (skip)
- Chinese tag assignment from predefined set
- Cost: ~$0.01-0.05 per run

## Data Sources / 数据源

| Source | Method | Events |
|--------|--------|--------|
| UoA Unievents | REST API | ~189 |
| Eventfinda | HTML + detail pages | ~50 |
| Eventbrite | `__SERVER_DATA__` JSON | ~30 |
| Meetup | JSON-LD | ~20 |
| Auckland Council | HTML + detail pages | ~20 |

## Automation / 自动化

GitHub Actions runs daily at 8:00 AM NZST:
1. Scrapes all sources
2. AI scores and tags events
3. Exports results to `output/`
4. Commits to repo

Set `ANTHROPIC_API_KEY` in repository secrets to enable AI scoring.

## Tech Stack

- Python 3.9+
- Pydantic v2 — data validation
- BeautifulSoup + lxml — HTML parsing
- Requests + urllib3 Retry — HTTP with rate limiting
- Claude API (Sonnet) — AI scoring & content generation

## License

MIT
