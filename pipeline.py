"""Pipeline orchestration: scrape → preprocess → AI → validate → export."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from ai_agent import AIAgent
from config import (
    LEAD_JSON_FIELDS,
    MIN_CONFIDENCE,
    MIN_OUTREACH_SCORE,
    OUTPUT_CSV,
    OUTPUT_JSON,
    PROJECT_ROOT,
    VALIDATE_WEBSITE_STATUS,
)
from history import filter_new_urls, load_crawled_domains, record_crawled_urls
from models import Lead, RawLeadCandidate
from preprocess import preprocess_page
from scraper import AsyncLeadScraper, ScraperConfig
from utils import get_logger, normalize_url
from validator import LeadValidator

logger = get_logger(__name__)


def save_leads(leads: list[Lead], append: bool = True) -> None:
    """Write clean JSON + CSV outputs."""
    json_path = PROJECT_ROOT / OUTPUT_JSON
    csv_path = PROJECT_ROOT / OUTPUT_CSV

    records = [lead.to_dict() for lead in leads]
    for rec in records:
        rec["tags"] = ", ".join(rec.get("tags") or [])

    # JSON — merge with existing
    existing: list[dict] = []
    if append and json_path.is_file():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            existing = data if isinstance(data, list) else data.get("leads", [])
        except (json.JSONDecodeError, OSError):
            existing = []

    merged = existing + records
    merged = _dedup_records(merged)
    json_path.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Saved %d total lead(s) → %s", len(merged), json_path)

    # CSV
    new_df = pd.DataFrame(records, columns=list(LEAD_JSON_FIELDS))
    if append and csv_path.is_file() and not new_df.empty:
        try:
            old = pd.read_csv(csv_path)
            df = pd.concat([old, new_df], ignore_index=True)
            df = df.drop_duplicates(
                subset=["business_name", "email", "website"],
                keep="first",
            )
        except Exception:
            df = new_df
    else:
        df = new_df

    if not df.empty:
        df.to_csv(csv_path, index=False)
    logger.info("Saved CSV → %s (%d rows)", csv_path, len(df))


def _dedup_records(records: list[dict]) -> list[dict]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict] = []
    for rec in records:
        key = (
            str(rec.get("business_name", "")).lower(),
            str(rec.get("email", "")).lower(),
            str(rec.get("website", "")).lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return out


async def run_pipeline(
    query: str,
    max_sites: int,
    max_depth: int,
    headless: bool,
    concurrency: int,
    skip_crawled: bool = False,
    min_confidence: float = MIN_CONFIDENCE,
    use_ai: bool | None = None,
) -> list[Lead]:
    """Full async AI lead generation pipeline."""
    crawled_domains: set[str] = set()
    if skip_crawled:
        crawled_domains = load_crawled_domains()
        if crawled_domains:
            logger.info("Skipping %d previously crawled domain(s)", len(crawled_domains))

    config = ScraperConfig(
        headless=headless,
        max_depth=max_depth,
        concurrency=concurrency,
    )

    agent = AIAgent(use_ai=use_ai if use_ai is not False else False)
    validator = LeadValidator(min_confidence=min_confidence)
    all_candidates: list[RawLeadCandidate] = []
    logger.info(
        "Pipeline AI=%s | min_confidence=%.2f | min_outreach_score=%.1f",
        use_ai,
        min_confidence,
        MIN_OUTREACH_SCORE,
    )

    async with AsyncLeadScraper(config, use_cache=skip_crawled) as scraper:
        urls = await scraper.discover_urls(
            query,
            max_sites=max_sites,
            exclude_domains=crawled_domains if skip_crawled else None,
            agent=agent,
        )
        if skip_crawled:
            urls = filter_new_urls(urls, crawled_domains)

        if not urls:
            logger.warning(
                "No new URLs to crawl. All results may be in history — "
                "try --force or a different query."
            )
            return []

        site_pages = await scraper.scrape_sites_parallel(urls, agent=agent)

        for site_url, pages in site_pages.items():
            website = normalize_url(site_url) or site_url
            for page in pages:
                if not page.html:
                    continue
                chunks = preprocess_page(page.html, page.url)
                if not chunks:
                    continue
                for chunk in chunks:
                    try:
                        found = agent.extract_from_chunk(chunk, website)
                        all_candidates.extend(found)
                    except Exception as exc:
                        logger.warning("Extract failed %s: %s", chunk.source_url, exc)

        if skip_crawled:
            record_crawled_urls(urls)

    leads = validator.validate_batch(all_candidates)
    logger.info("After validation: %d lead(s)", len(leads))

    if not leads and all_candidates:
        logger.warning(
            "[FORCE] No leads passed validation; force-accepting best %d candidate(s)",
            min(5, len(all_candidates)),
        )
        for cand in all_candidates[:5]:
            leads.append(cand.to_lead())

    if VALIDATE_WEBSITE_STATUS:
        leads = await validator.filter_unreachable_websites(leads)
        logger.info("After website filter: %d lead(s)", len(leads))

    scored: list[Lead] = []
    for lead in leads:
        try:
            lead.outreach_score = agent.score_lead_model(lead)
        except Exception as exc:
            logger.warning("Score failed for %s: %s", lead.business_name, exc)
            lead.outreach_score = agent._heuristic_score(lead)

        if lead.confidence_score < min_confidence:
            logger.debug(
                "Dropped %s: confidence %.2f < %.2f",
                lead.business_name,
                lead.confidence_score,
                min_confidence,
            )
            continue
        # We no longer drop based on MIN_OUTREACH_SCORE, so we keep all leads and sort them later
        scored.append(lead)

    if not scored and leads:
        logger.warning(
            "No leads passed scoring filters; returning %d best validated lead(s) instead",
            len(leads),
        )
        for lead in leads:
            if lead.outreach_score <= 0:
                try:
                    lead.outreach_score = agent.score_lead_model(lead)
                except Exception:
                    lead.outreach_score = agent._heuristic_score(lead)
        leads = sorted(leads, key=lambda lead: lead.outreach_score, reverse=True)
        save_leads(leads)
        return leads

    leads = scored
    leads.sort(key=lambda lead: lead.outreach_score, reverse=True)
    logger.info("Final leads after AI scoring filter: %d", len(leads))

    save_leads(leads)
    return leads
