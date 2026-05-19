"""Central configuration for the AI lead generation pipeline."""

from __future__ import annotations

import os
from pathlib import Path

# Paths
PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT
OUTPUT_JSON = "leads.json"
OUTPUT_CSV = "leads_output.csv"
OUTPUT_TXT = "leads_output.txt"
CRAWLED_HISTORY_FILE = "crawled_history.json"
VISITED_CACHE_FILE = "visited_urls.json"
LOG_FILE = "lead_scraper.log"

# Search engines
SEARCH_ENGINES = ("google", "bing", "duckduckgo")  # tried in order
GOOGLE_URL = "https://www.google.com"
DDG_URL = "https://duckduckgo.com"
BING_URL = "https://www.bing.com"
GOOGLE_SEARCH_INPUT = 'textarea[name="q"], input[name="q"]'
GOOGLE_RESULT_LINK_SELECTORS = [
    "div.g a[href]",
    "div#search a[href]",
]
DDG_SEARCH_INPUT_SELECTORS = [
    'input[name="q"]',
    "#searchbox_input",
    'input[aria-label="Search"]',
    'input[type="search"]',
]
BING_SEARCH_INPUT = 'input[name="q"], #sb_form_q'
DDG_RESULT_LINK_SELECTORS = [
    "article[data-testid='result'] h2 a",
    "a[data-testid='result-title-a']",
    "#links .result__a",
    "li[data-layout='organic'] h2 a",
]
BING_RESULT_LINK_SELECTORS = [
    "li.b_algo h2 a",
    "#b_results h2 a",
]
DDG_EXCLUDED_DOMAINS = (
    "duckduckgo.com",
    "duck.com",
    "bing.com",
    "microsoft.com",
    "spreadprivacy.com",
)

# Crawling
MAX_SITES_DEFAULT = 5
MAX_DEPTH_DEFAULT = 2
MAX_PAGES_PER_SITE = 12
MAX_INTERNAL_LINKS_PER_PAGE = 10
MAX_SCROLL_ATTEMPTS = 5
SCROLL_PAUSE_MS = 1500
NAVIGATION_TIMEOUT_MS = 30_000
SELECTOR_TIMEOUT_MS = 45_000
PAGE_LOAD_WAIT = "domcontentloaded"
SEARCH_RETRIES = 3
PAGE_LOAD_RETRIES = 2
RETRY_PAUSE_MS = 1500
MIN_DELAY_SEC = 0.8
MAX_DELAY_SEC = 2.5
DEFAULT_CONCURRENCY = 4

# Pre-processing
MAX_CHUNK_CHARS = 3500
MIN_CHUNK_CHARS = 80

# Local AI (Ollama) — no API keys required
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")
OLLAMA_TIMEOUT_SEC = int(os.environ.get("OLLAMA_TIMEOUT_SEC", "30"))
OLLAMA_MAX_RETRIES = 2
OLLAMA_MAX_TEXT_CHARS = 2000
USE_AI = True  # Ollama local by default; use --no-ai to disable

# Filtering thresholds
MIN_OUTREACH_SCORE = 3.0

# Validation
MIN_CONFIDENCE = 0.2
VALIDATE_WEBSITE_STATUS = False
WEBSITE_CHECK_TIMEOUT_SEC = 8
DISPOSABLE_EMAIL_DOMAINS = frozenset(
    {
        "mailinator.com",
        "guerrillamail.com",
        "tempmail.com",
        "yopmail.com",
        "10minutemail.com",
    }
)

# Email / phone patterns (heuristic fallback)
EMAIL_PATTERN = (
    r"(?<![\w.])"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9._%+-]{0,62}[a-zA-Z0-9])?"
    r"@"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+"
    r"(?![\w.])"
)
PHONE_PATTERN = (
    r"(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}"
    r"|\+?\d{1,3}[-.\s]?\d{6,14}"
)

JUNK_PATH_FRAGMENTS = (
    "login", "signin", "signup", "register", "cart", "checkout",
    "privacy", "terms", "cookie", "wp-admin", "javascript:", "mailto:", "tel:", "#",
)

# Output schema
LEAD_JSON_FIELDS = (
    "business_name",
    "website",
    "email",
    "phone",
    "location",
    "niche",
    "tags",
    "confidence_score",
    "outreach_score",
    "source_url",
)

OUTPUT_COLUMNS = LEAD_JSON_FIELDS

# Browser stealth
BROWSER_TYPE = "chromium"
USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
]
BROWSER_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]
STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {} };
"""
