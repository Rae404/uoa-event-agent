# Notion 数据库配置指南

## 1. 创建 Integration

1. 打开 https://www.notion.so/my-integrations
2. 点击 "New integration"
3. 名称填 `UoA Event Agent`
4. 权限选择：Read content, Insert content, Update content
5. 保存后复制 `Internal Integration Secret` → 这就是 `NOTION_TOKEN`

## 2. 创建数据库

在 Notion 中创建一个新的 Database（Full page），添加以下属性：

| 属性名 | 类型 | 说明 |
|--------|------|------|
| Name | Title | 活动标题（默认已有） |
| Date | Date | 活动日期 |
| Source | Select | 数据源 (eventfinda/eventbrite/meetup/uoa/council) |
| Priority | Select | 优先级 (S/A/B/C) |
| Score | Number | AI 评分 0-100 |
| Cost | Text | 费用 (free/$15/unknown) |
| Location | Text | 地点 |
| URL | URL | 活动链接 |
| Tags | Multi-select | 中文标签 |

### Select 选项预设

**Source:**
- eventfinda, eventbrite, meetup, uoa, council

**Priority:**
- S (颜色: 红), A (颜色: 橙), B (颜色: 蓝), C (颜色: 灰)

**Tags (Multi-select):**
- 新手友好, i人友好, 练英语, 交朋友, 找工有帮助, 免费薅羊毛, 有吃喝, 涨知识, 户外活动, 文化体验, 适合拍照

## 3. 连接 Integration

1. 打开刚创建的数据库页面
2. 点击右上角 `...` → `Connect to` → 选择 `UoA Event Agent`

## 4. 获取 Database ID

数据库页面 URL 格式：
```
https://www.notion.so/xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx?v=...
```
`xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` 就是 `NOTION_DATABASE_ID`（32位十六进制）

## 5. 配置 .env

```bash
NOTION_TOKEN=secret_xxxxxxxxx
NOTION_DATABASE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

## 6. 测试

```bash
python -m src.cli --sources uoa --notion --verbose
```

## 7. GitHub Actions

在 repo Settings → Secrets and variables → Actions 中添加：
- `NOTION_TOKEN`
- `NOTION_DATABASE_ID`
