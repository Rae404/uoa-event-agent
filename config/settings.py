"""Configuration for the UoA Event Agent."""

# --- Data Source URLs ---
EVENTFINDA_SEARCH_URL = "https://www.eventfinda.co.nz/whatson/events/auckland"
EVENTFINDA_BASE_URL = "https://www.eventfinda.co.nz"

EVENTBRITE_SEARCH_URL = "https://www.eventbrite.co.nz/d/new-zealand--auckland/events/"

MEETUP_SEARCH_URL = "https://www.meetup.com/find/?location=nz--Auckland&source=EVENTS"

UOA_EVENTS_URL = "https://unievents.auckland.ac.nz/home"

AUCKLAND_COUNCIL_EVENTS_URL = "https://ourauckland.aucklandcouncil.govt.nz/events"

# --- Rate Limiting ---
REQUEST_DELAY_SECONDS = 1.5
MAX_RETRIES = 3
REQUEST_TIMEOUT = 15

# --- Scoring Weights (Rule-based pre-scoring) ---
SCORE_FREE_EVENT = 15
SCORE_NEAR_UOA = 10
SCORE_KEYWORD_MATCH = 5
SCORE_SOURCE_TRUST = {
    "uoa": 15,
    "council": 10,
    "eventfinda": 8,
    "eventbrite": 8,
    "meetup": 5,
}
SCORE_WITHIN_7_DAYS = 10
SCORE_MINIMUM_THRESHOLD = 15  # below this → skip AI scoring

# --- Scoring Keywords ---
POSITIVE_KEYWORDS = [
    "networking", "career", "workshop", "free", "social", "student",
    "graduate", "internship", "job", "hackathon", "meetup", "community",
    "cultural", "chinese", "asian", "international", "volunteer",
    "food", "music", "sport", "wellness", "mental health",
]

# --- UoA Proximity Keywords (location matching) ---
UOA_LOCATION_KEYWORDS = [
    "university of auckland", "uoa", "auckland uni", "symonds",
    "grafton", "city campus", "epsom", "newmarket", "albert park",
    "auckland cbd", "queen street", "k road", "karangahape",
]

# --- AI Scoring ---
AI_MODEL = "gpt-4o-mini"
AI_BATCH_SIZE = 12
AI_MAX_TOKENS = 4096

# --- Export ---
DEFAULT_OUTPUT_DIR = "output"
DEFAULT_LIMIT_PER_SOURCE = 50

# --- Tags (Chinese labels for the target audience) ---
AVAILABLE_TAGS = [
    "新手友好", "i人友好", "练英语", "交朋友",
    "找工有帮助", "免费薅羊毛", "有吃喝", "涨知识",
    "户外活动", "文化体验", "适合拍照",
]
