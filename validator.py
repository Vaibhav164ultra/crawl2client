"""Lead validation: dedup, email/website checks, confidence filtering."""

from __future__ import annotations

import asyncio
import re
from typing import Iterable

import requests

from config import (
    DISPOSABLE_EMAIL_DOMAINS,
    EMAIL_PATTERN,
    MIN_CONFIDENCE,
    VALIDATE_WEBSITE_STATUS,
    WEBSITE_CHECK_TIMEOUT_SEC,
)
from models import Lead, RawLeadCandidate
from utils import get_logger, normalize_url

logger = get_logger(__name__)

_EMAIL_RE = re.compile(EMAIL_PATTERN, re.IGNORECASE)


class LeadValidator:
    """Validate and deduplicate extracted leads."""

    def __init__(self, min_confidence: float = MIN_CONFIDENCE) -> None:
        self.min_confidence = min_confidence
        self._seen: set[tuple[str, str, str]] = set()

    def validate_email(self, email: str) -> bool:
        if not email:
            return True
        email = email.lower().strip()
        if not _EMAIL_RE.fullmatch(email):
            return False
        domain = email.split("@", 1)[-1]
        if domain in DISPOSABLE_EMAIL_DOMAINS:
            return False
        if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", domain):
            return False
        return True

    def validate_phone(self, phone: str) -> bool:
        if not phone:
            return True
        digits = re.sub(r"\D", "", phone)
        return 10 <= len(digits) <= 15

    def validate_candidate(self, raw: RawLeadCandidate) -> Lead | None:
        if raw.confidence_score < self.min_confidence:
            logger.debug("Dropped low confidence: %s", raw.business_name)
            return None

        if not raw.business_name or len(raw.business_name) < 2:
            return None

        if not raw.email and not raw.phone:
            return None

        if raw.email and not self.validate_email(raw.email):
            logger.debug("Invalid email: %s", raw.email)
            return None

        if raw.phone and not self.validate_phone(raw.phone):
            logger.debug("Invalid phone: %s", raw.phone)
            return None

        website = raw.website
        if website:
            normalized = normalize_url(website)
            website = normalized or website

        lead = Lead(
            business_name=raw.business_name.strip(),
            website=website,
            email=raw.email.strip().lower() if raw.email else "",
            phone=raw.phone.strip() if raw.phone else "",
            location=raw.location.strip() if raw.location else "",
            niche=raw.niche.strip() if raw.niche else "general",
            tags=list(raw.tags) if raw.tags else ["local business"],
            confidence_score=raw.confidence_score,
            source_url=raw.source_url,
        )

        key = lead.dedup_key()
        if key in self._seen:
            return None
        self._seen.add(key)
        return lead

    def validate_batch(self, candidates: Iterable[RawLeadCandidate]) -> list[Lead]:
        leads: list[Lead] = []
        for raw in candidates:
            lead = self.validate_candidate(raw)
            if lead:
                leads.append(lead)
        logger.info("Validated %d lead(s) from candidates", len(leads))
        return leads

    def _check_website_sync(self, url: str) -> bool:
        """HEAD/GET check using requests (runs in thread pool)."""
        if not url or not VALIDATE_WEBSITE_STATUS:
            return True
        normalized = normalize_url(url)
        if not normalized:
            return False
        headers = {"User-Agent": "LeadScraper/1.0"}
        try:
            resp = requests.head(
                normalized,
                allow_redirects=True,
                timeout=WEBSITE_CHECK_TIMEOUT_SEC,
                headers=headers,
            )
            if resp.status_code < 400:
                return True
        except Exception:
            pass
        try:
            resp = requests.get(
                normalized,
                allow_redirects=True,
                timeout=WEBSITE_CHECK_TIMEOUT_SEC,
                headers=headers,
            )
            return resp.status_code < 400
        except Exception as exc:
            logger.debug("Website check failed %s: %s", url, exc)
            return False

    async def check_website_reachable(self, url: str) -> bool:
        return await asyncio.to_thread(self._check_website_sync, url)

    async def filter_unreachable_websites(self, leads: list[Lead]) -> list[Lead]:
        if not VALIDATE_WEBSITE_STATUS:
            return leads

        async def _check(lead: Lead) -> Lead | None:
            if not lead.website:
                return lead
            ok = await self.check_website_reachable(lead.website)
            if ok:
                return lead
            logger.info("Dropped unreachable website: %s", lead.website)
            return None

        results = await asyncio.gather(*[_check(lead) for lead in leads])
        return [lead for lead in results if lead is not None]

    def filter_by_outreach_score(
        self,
        leads: list[Lead],
        min_score: float = 4.0,
    ) -> list[Lead]:
        return [lead for lead in leads if lead.outreach_score >= min_score]
