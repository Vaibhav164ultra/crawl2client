# AI Lead Scraper

Production-oriented hybrid lead generation: **async Playwright scraping** + **LLM extraction/validation** + **structured JSON/CSV output**.

## Architecture

```
Search (DuckDuckGo / Bing)
    → Async site crawl (stealth, cached URLs)
    → HTML preprocess (BeautifulSoup, chunked text)
    → AI extraction agent (OpenAI or heuristics)
    → Validation (email, website, dedup, confidence ≥ 0.6)
    → Quality scorer agent (outreach 0–10)
    → leads.json + leads_output.csv
```

## Modules

| File | Role |
|------|------|
| `main.py` | CLI + asyncio pipeline entry |
| `scraper.py` | Async Playwright search + crawl |
| `preprocess.py` | HTML cleaning + text chunks |
| `ai_agent.py` | LLM extract + lead quality scorer |
| `validator.py` | Dedup, email/phone/website validation |
| `pipeline.py` | Orchestrates full flow + export |
| `utils.py` | Logging, URLs, retries |
| `history.py` | Skip previously crawled domains |

## Setup

```powershell
cd C:\Users\Vaibhav\.cursor\projects\lead-scraper
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

### Local AI (Ollama — default)

```powershell
# In a separate terminal, keep Ollama running:
ollama run llama3.2

# Then run the scraper (no API keys needed):
python main.py -q "restaurants bangalore" -n 5
```

Uses `http://localhost:11434` with model **llama3.2**. Override via:

```powershell
$env:OLLAMA_URL = "http://localhost:11434/api/generate"
$env:OLLAMA_MODEL = "llama3.2"
```

Use `--no-ai` for regex/heuristic-only mode if Ollama is offline.

## Usage

```powershell
# Ollama local AI (default)
python main.py -q "restaurants bangalore" -n 5 -d 2

# Heuristics only (Ollama off)
python main.py -q "digital agencies mumbai" -n 3 --no-ai

# Visible browser for debugging
python main.py -q "cafes delhi" --show-browser -v

# Re-crawl previously seen domains
python main.py -q "..." --force
```

## Output

**`leads.json`** — clean structured leads:

```json
[
  {
    "business_name": "Example Cafe",
    "email": "hello@example.com",
    "website": "https://example.com",
    "niche": "restaurant",
    "location": "Bangalore",
    "confidence_score": 0.85,
    "outreach_score": 8.2,
    "tags": ["restaurant", "local business"]
  }
]
```

**`leads_output.csv`** — same fields for spreadsheets.

## Flags

| Flag | Description |
|------|-------------|
| `-q` | Search query |
| `-n` | Max sites to crawl |
| `-d` | Crawl depth (1–3) |
| `-w` | Parallel workers |
| `--no-ai` | Disable Ollama; regex/heuristic only |
| `--min-confidence` | Drop leads below threshold (default 0.6) |
| `--force` | Ignore crawl history |
| `-v` | Debug logging → `lead_scraper.log` |
