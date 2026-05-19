"""Local LLM extraction + scoring via Ollama (llama3.2)."""

from __future__ import annotations

import json
import re
import time
from typing import Any

import requests

from config import (
    OLLAMA_MAX_RETRIES,
    OLLAMA_MAX_TEXT_CHARS,
    OLLAMA_MODEL,
    OLLAMA_TIMEOUT_SEC,
    OLLAMA_URL,
)
from models import Lead, RawLeadCandidate
from preprocess import TextChunk
from utils import (
    extract_emails_from_text,
    extract_phones_from_text,
    extract_urls_from_text,
    get_logger,
    get_registrable_domain,
    normalize_url,
)

logger = get_logger(__name__)

NICHE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "restaurant": ("restaurant", "menu", "dining", "cuisine", "cafe", "bistro"),
    "agency": ("agency", "marketing", "seo", "digital", "consulting"),
    "ecommerce": ("shop", "store", "cart", "buy now", "ecommerce", "products"),
    "startup": ("startup", "founder", "saas", "venture", "seed"),
    "local business": ("plumber", "dentist", "clinic", "salon", "repair", "local"),
}


# ---------------------------------------------------------------------------
# Ollama client
# ---------------------------------------------------------------------------


def call_ollama(prompt: str, retries: int | None = None) -> str:
    """Call local Ollama generate API. Returns raw response text."""
    max_attempts = retries if retries is not None else OLLAMA_MAX_RETRIES
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            logger.debug("[Ollama] Request attempt %d/%d", attempt, max_attempts)
            res = requests.post(
                OLLAMA_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                },
                timeout=OLLAMA_TIMEOUT_SEC,
            )
            res.raise_for_status()
            text = res.json().get("response", "")
            logger.debug("[Ollama] Response length: %d chars", len(text))
            return text
        except requests.Timeout as exc:
            last_error = exc
            logger.warning("[Ollama] Timeout (attempt %d): %s", attempt, exc)
        except requests.RequestException as exc:
            last_error = exc
            logger.warning("[Ollama] Request failed (attempt %d): %s", attempt, exc)
        except Exception as exc:
            last_error = exc
            logger.warning("[Ollama] Error (attempt %d): %s", attempt, exc)

        if attempt < max_attempts:
            time.sleep(1.0 * attempt)

    logger.error("[Ollama Error]: %s", last_error)
    return ""


def _prepare_text_for_ollama(text: str, max_chars: int = OLLAMA_MAX_TEXT_CHARS) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"\s+", " ", text).strip()
    paragraphs = [
        para.strip()
        for para in re.split(r"\n{2,}|\r\n|\n", cleaned)
        if len(para.strip()) >= 50
    ]
    meaningful: list[str] = []
    for para in paragraphs:
        if len(re.findall(r"[A-Za-z]", para)) < 30:
            continue
        if para.count("http") + para.count("www.") > 2 and "@" not in para:
            continue
        meaningful.append(para)
    if not meaningful:
        meaningful = paragraphs or [cleaned]

    selected: list[str] = []
    total = 0
    for para in meaningful:
        if total + len(para) + 2 > max_chars:
            break
        selected.append(para)
        total += len(para) + 2
    if not selected:
        selected = [meaningful[0][:max_chars]]
    return "\n\n".join(selected).strip()


def _extract_json_candidates(raw: str) -> list[str]:
    if not raw or not raw.strip():
        return []

    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.I)
    if fence:
        text = fence.group(1).strip()

    candidates: list[str] = []
    candidates.extend(re.findall(r"\[.*?\]", text, re.S))
    if not candidates:
        arr_start = text.find("[")
        arr_end = text.rfind("]")
        if arr_start != -1 and arr_end > arr_start:
            candidates.append(text[arr_start : arr_end + 1])

    if not candidates:
        candidates.extend(re.findall(r"\{[\s\S]*?\}", text, re.S))

    if not candidates:
        candidates.append(text)

    return sorted(set(candidates), key=len, reverse=True)


def _parse_json_candidate(candidate: str) -> Any:
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        logger.debug("[AI JSON] parse failed: %s", exc)
        cleaned = re.sub(r",\s*([\]}])", r"\1", candidate)
        if cleaned != candidate:
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError as exc2:
                logger.debug("[AI JSON] repair parse failed: %s", exc2)
        return None


def _extract_json_from_response(raw: str) -> Any:
    """Parse JSON from model output; strip prose and extract bracketed JSON blocks."""
    if not raw or not raw.strip():
        return []

    logger.debug("[AI OUTPUT] raw=%s", raw[:300])
    candidates = _extract_json_candidates(raw)
    logger.debug("[AI JSON] found %d candidate block(s)", len(candidates))

    for candidate in candidates:
        parsed = _parse_json_candidate(candidate)
        if parsed is not None:
            logger.debug("[AI JSON] parsed candidate len=%d", len(candidate))
            return parsed

    logger.debug("[AI JSON] no valid JSON parsed from candidates")
    return []


def _normalize_lead_list(data: Any) -> list[dict[str, Any]]:
    """Accept array or {\"leads\": [...]} wrapper."""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        if "leads" in data and isinstance(data["leads"], list):
            return [item for item in data["leads"] if isinstance(item, dict)]
        if "business_name" in data:
            return [data]
    return []


# ---------------------------------------------------------------------------
# Lead extraction (Ollama)
# ---------------------------------------------------------------------------


def _parse_ollama_json(raw_output: str) -> list[dict[str, Any]]:
    parsed = _extract_json_from_response(raw_output)
    return _normalize_lead_list(parsed)


def _repair_ollama_json(raw_output: str) -> list[dict[str, Any]]:
    if not raw_output:
        return []
    repair_prompt = f"Fix this JSON. Return ONLY valid JSON:\n{raw_output}"
    raw_output = call_ollama(repair_prompt, retries=1)
    logger.debug("[AI OUTPUT] repair raw=%s", raw_output[:300])
    return _parse_ollama_json(raw_output)


def _fallback_extract_leads(text: str, source_url: str, website: str) -> list[dict[str, Any]]:
    emails = extract_emails_from_text(text)
    phones = extract_phones_from_text(text)
    urls = extract_urls_from_text(text)
    if not (emails or phones or urls):
        return []

    site_url = website or source_url or (urls[0] if urls else "")
    if site_url and not site_url.startswith("http"):
        site_url = normalize_url(site_url) or site_url

    domain = get_registrable_domain(site_url) if site_url else ""
    default_name = domain.replace(".", " ").replace("-", " ").title() or "Business"

    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    if emails:
        for email in sorted(emails)[:5]:
            lead = {
                "business_name": default_name,
                "website": site_url,
                "email": email,
                "phone": sorted(phones)[0] if phones else "",
                "location": "",
                "niche": "",
                "confidence_score": 0.55,
            }
            key = (lead["email"], lead["website"], lead["phone"])
            if key not in seen:
                seen.add(key)
                results.append(lead)
    elif phones:
        for phone in sorted(phones)[:3]:
            lead = {
                "business_name": default_name,
                "website": site_url,
                "email": "",
                "phone": phone,
                "location": "",
                "niche": "",
                "confidence_score": 0.50,
            }
            key = (lead["email"], lead["website"], lead["phone"])
            if key not in seen:
                seen.add(key)
                results.append(lead)
    else:
        for url in urls[:3]:
            normalized = normalize_url(url) or url
            lead = {
                "business_name": default_name,
                "website": normalized,
                "email": "",
                "phone": "",
                "location": "",
                "niche": "",
                "confidence_score": 0.45,
            }
            key = (lead["email"], lead["website"], lead["phone"])
            if key not in seen:
                seen.add(key)
                results.append(lead)

    return results


def extract_leads(text: str, source_url: str = "", website: str = "") -> list[dict[str, Any]]:
    """
    Extract structured leads from raw text using local Ollama.
    Returns list of lead dicts (may be empty on failure).
    """
    clipped = _prepare_text_for_ollama(text, OLLAMA_MAX_TEXT_CHARS)
    prompt = (
        "Extract business leads from the following text. Return ONLY a valid JSON array.\n"
        "Do not include explanation, text, markdown, or comments.\n"
        "Start with [ and end with ] only.\n"
        "Each item must have: business_name, website, email, phone, location, niche, confidence_score (0.5-1.0).\n"
        "confidence_score: 0.5-1.0 for real leads, 0.0 if unsure.\n"
        "Do not hallucinate. Skip any missing fields.\n"
        "Do not include any extra text outside the JSON array.\n"
        f"Source page: {source_url}\n"
        f"Website: {website}\n"
        "Text:\n"
        f"{clipped}\n"
    )

    raw_output = call_ollama(prompt)
    logger.debug("[AI OUTPUT] first 300 chars: %s", raw_output[:300])
    data = _parse_ollama_json(raw_output)

    if not data:
        logger.debug("[AI] First parse failed, auto-repairing JSON")
        data = _repair_ollama_json(raw_output)

    if not data:
        logger.debug("[AI] Retry with stricter JSON prompt")
        retry_prompt = (
            "Extract business leads. Return ONLY valid JSON array.\n"
            "[{\"business_name\": \"\", \"website\": \"\", \"email\": \"\", \"phone\": \"\", \"location\": \"\", \"niche\": \"\", \"confidence_score\": 0.7}]\n"
            f"Source: {source_url}\n{website}\n"
            f"{clipped}\n"
        )
        raw_output = call_ollama(retry_prompt)
        logger.debug("[AI OUTPUT] retry first 300 chars: %s", raw_output[:300])
        data = _parse_ollama_json(raw_output)

    fallback = _fallback_extract_leads(text, source_url, website)
    
    if data:
        logger.info("[AI] Extracted %d lead(s)", len(data))
        if fallback:
            logger.info("[AI] Appending %d regex fallback lead(s) for max accuracy", len(fallback))
            data.extend(fallback)
        return data

    logger.warning("[AI ERROR] Invalid JSON from Ollama after retry")
    if fallback:
        logger.warning("[AI FALLBACK] Returning %d partial lead(s) from regex backup", len(fallback))
        return fallback

    return []


# ---------------------------------------------------------------------------
# Lead scoring (Ollama)
# ---------------------------------------------------------------------------


def score_lead(lead: dict[str, Any] | Lead) -> float:
    """Rate lead quality 0–10 using local Ollama."""
    if isinstance(lead, Lead):
        payload = lead.to_dict()
    else:
        payload = lead

    prompt = f"""
Rate this business lead from 0 to 10.

Criteria:
- Has email or phone?
- Real business?
- Useful for outreach?

Return ONLY a number.

Lead:
{json.dumps(payload, ensure_ascii=False)}
"""

    output = call_ollama(prompt, retries=2)

    if not output:
        return 0.0

    # Extract first number in response
    match = re.search(r"(\d+(?:\.\d+)?)", output.strip())
    if match:
        score = float(match.group(1))
        return max(0.0, min(10.0, score))

    try:
        return max(0.0, min(10.0, float(output.strip())))
    except ValueError:
        logger.warning("[AI] Could not parse score: %r", output[:80])
        return 0.0


# ---------------------------------------------------------------------------
# AIAgent — pipeline integration (Ollama + heuristic fallback)
# ---------------------------------------------------------------------------


class AIAgent:
    """Extraction and quality-scoring via Ollama with heuristic fallback."""

    def __init__(self, use_ai: bool = True) -> None:
        self.use_ai = use_ai
        self._ollama_ok: bool | None = None
        if self.use_ai:
            self._ollama_ok = self._check_ollama()
            if not self._ollama_ok:
                logger.warning(
                    "Ollama not reachable at %s — using heuristics. "
                    "Start with: ollama run %s",
                    OLLAMA_URL,
                    OLLAMA_MODEL,
                )

    def _check_ollama(self) -> bool:
        try:
            base = OLLAMA_URL.replace("/api/generate", "")
            res = requests.get(f"{base}/api/tags", timeout=5)
            res.raise_for_status()
            logger.info("Ollama connected | model=%s", OLLAMA_MODEL)
            return True
        except Exception as exc:
            logger.debug("Ollama health check failed: %s", exc)
            return False

    def extract_from_chunk(
        self,
        chunk: TextChunk,
        website: str,
    ) -> list[RawLeadCandidate]:
        if self.use_ai and self._ollama_ok:
            try:
                items = extract_leads(
                    chunk.text,
                    source_url=chunk.source_url,
                    website=website,
                )
                if items:
                    return [
                        self._normalize_candidate(item, chunk.source_url, website)
                        for item in items
                    ]
            except Exception as exc:
                logger.warning("Ollama extraction failed, fallback: %s", exc)

        return self._heuristic_extract(chunk.text, chunk.source_url, website)

    def score_lead_model(self, lead: Lead) -> float:
        """Score a Lead model (used by pipeline)."""
        if self.use_ai and self._ollama_ok:
            try:
                return score_lead(lead.to_dict())
            except Exception as exc:
                logger.warning("Ollama scoring failed, heuristic: %s", exc)
        return self._heuristic_score(lead)

    def select_best_links(self, links: list[str]) -> list[str]:
        """Ask AI to select the top 3-5 links most likely to contain contact info."""
        if not self.use_ai or not self._ollama_ok or not links:
            return links[:5]

        prompt = (
            "Select links most likely to contain business contact info.\n"
            "Prefer:\n- official websites\n- business directories\n- aggregators (e.g. Justdial, Yelp, LBB)\n- contact pages\n"
            "Avoid:\n- ads\n- news\n- job listings\n"
            "Return ONLY a JSON array of strings containing the selected URLs.\n"
            "Links:\n" + "\n".join(links)
        )

        try:
            raw_output = call_ollama(prompt, retries=1)
            parsed = _extract_json_candidates(raw_output)
            for candidate in parsed:
                data = _parse_json_candidate(candidate)
                if isinstance(data, list) and all(isinstance(x, str) for x in data):
                    selected = [u for u in data if u in links]
                    if selected:
                        return selected[:5]
        except Exception as exc:
            logger.warning("AI link selection failed: %s", exc)

        # Fallback heuristic
        return links[:5]

    def evaluate_page_navigation(self, text: str, links: list[str]) -> dict[str, Any]:
        """
        Ask AI if the page contains contact info.
        If yes, it should return 'has_contact_info': true
        If no, it should suggest 'next_link' from the provided links.
        Returns a dict.
        """
        # Fast Heuristic: If we see emails or phones, we have contact info.
        fast_emails = extract_emails_from_text(text)
        fast_phones = extract_phones_from_text(text)
        has_info = bool(fast_emails or fast_phones)

        # Fast Heuristic: Look for "contact", "about", "team" links
        best_link = None
        for link in links:
            lower_link = link.lower()
            if "contact" in lower_link or "about" in lower_link or "team" in lower_link:
                best_link = link
                break

        if not self.use_ai or not self._ollama_ok:
            return {"has_contact_info": has_info, "next_link": best_link or (links[0] if links else None)}

        if has_info and best_link:
            # We already have info and a good next link, skip LLM to save time
            return {"has_contact_info": True, "next_link": best_link}

        clipped_text = _prepare_text_for_ollama(text, 1500)
        clipped_links = links[:20]  # Limit to top 20 links to avoid huge prompts

        prompt = (
            "Analyze the text and decide if it contains business contact information (emails, phone numbers, company name).\n"
            "If it does, set 'has_contact_info' to true.\n"
            "If it does NOT, select the best URL from the provided 'Available Links' to click next (e.g., a 'Contact Us' or 'About' page), and set it as 'next_link'.\n"
            "Return ONLY a JSON object with this schema: {\"has_contact_info\": boolean, \"next_link\": string|null}\n"
            "Available Links:\n" + "\n".join(clipped_links) + "\n\n"
            "Text:\n" + clipped_text
        )

        try:
            raw_output = call_ollama(prompt, retries=1)
            parsed = _extract_json_candidates(raw_output)
            for candidate in parsed:
                data = _parse_json_candidate(candidate)
                if isinstance(data, dict):
                    has_contact = bool(data.get("has_contact_info")) or has_info
                    next_link = data.get("next_link") or best_link
                    if next_link and next_link not in clipped_links:
                        # Sometimes it might invent a link or slightly modify it, let's try to match
                        for l in clipped_links:
                            if next_link in l or l in next_link:
                                next_link = l
                                break
                    return {"has_contact_info": has_contact, "next_link": next_link}
        except Exception as exc:
            logger.warning("AI navigation evaluation failed: %s", exc)

        return {"has_contact_info": has_info, "next_link": best_link or (links[0] if links else None)}

    def _normalize_candidate(
        self,
        item: dict[str, Any],
        source_url: str,
        website: str,
    ) -> RawLeadCandidate:
        conf = item.get("confidence_score", 0.5)
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            conf = 0.5

        site = str(item.get("website") or website).strip()
        if site and not site.startswith("http"):
            site = normalize_url(site, base=website) or website

        tags = self._infer_tags(str(item.get("niche", "")), str(item.get("business_name", "")))

        return RawLeadCandidate(
            business_name=str(item.get("business_name") or "").strip(),
            website=site or website,
            email=str(item.get("email") or "").strip().lower(),
            phone=str(item.get("phone") or "").strip(),
            location=str(item.get("location") or "").strip(),
            niche=str(item.get("niche") or "").strip(),
            tags=tags,
            confidence_score=max(0.0, min(1.0, conf)),
            source_url=source_url,
        )

    def _heuristic_extract(
        self,
        text: str,
        source_url: str,
        website: str,
    ) -> list[RawLeadCandidate]:
        emails = sorted(extract_emails_from_text(text))
        phones = sorted(extract_phones_from_text(text))
        if not emails and not phones:
            return []

        name = self._guess_name(text, website)
        niche, tags = self._detect_niche(text)
        conf = 0.65 if (emails and phones) else 0.6

        candidates: list[RawLeadCandidate] = []
        if emails:
            for email in emails[:5]:
                candidates.append(
                    RawLeadCandidate(
                        business_name=name,
                        website=website,
                        email=email,
                        phone=phones[0] if phones else "",
                        location="",
                        niche=niche,
                        tags=tags,
                        confidence_score=conf,
                        source_url=source_url,
                    )
                )
        else:
            for phone in phones[:3]:
                candidates.append(
                    RawLeadCandidate(
                        business_name=name,
                        website=website,
                        email="",
                        phone=phone,
                        location="",
                        niche=niche,
                        tags=tags,
                        confidence_score=conf - 0.05,
                        source_url=source_url,
                    )
                )
        return candidates

    @staticmethod
    def _guess_name(text: str, website: str) -> str:
        for line in text.splitlines()[:8]:
            line = line.strip()
            if 3 <= len(line) <= 80 and not line.startswith("http"):
                return line
        domain = get_registrable_domain(website)
        return domain.split(".")[0].replace("-", " ").title()

    @staticmethod
    def _detect_niche(text: str) -> tuple[str, list[str]]:
        lower = text.lower()
        tags: list[str] = []
        niche = ""
        for tag, keywords in NICHE_KEYWORDS.items():
            if any(kw in lower for kw in keywords):
                tags.append(tag)
                if not niche:
                    niche = tag
        if not tags:
            tags.append("local business")
            niche = "general"
        return niche, tags

    @staticmethod
    def _infer_tags(niche: str, name: str) -> list[str]:
        combined = f"{niche} {name}".lower()
        tags: list[str] = []
        for tag, keywords in NICHE_KEYWORDS.items():
            if any(kw in combined for kw in keywords):
                tags.append(tag)
        return tags or ["local business"]

    @staticmethod
    def _heuristic_score(lead: Lead) -> float:
        score = 3.0
        if lead.email:
            score += 2.5
        if lead.phone:
            score += 2.0
        if lead.business_name and len(lead.business_name) > 2:
            score += 1.5
        if lead.website:
            score += 1.0
        if lead.confidence_score >= 0.8:
            score += 1.0
        return min(10.0, score)
