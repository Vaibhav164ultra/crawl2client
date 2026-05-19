"""Modular page-level field extractors (email, phone, name, address, contact)."""

from __future__ import annotations

import json
import re
from html import unescape
from typing import Any
from urllib.parse import urlparse

from config import (
    CONTACT_PATH_KEYWORDS,
    CUISINE_META_KEYS,
    PHONE_PATTERN,
    RESTAURANT_KEYWORDS,
    SOCIAL_HOST_FRAGMENTS,
)
from utils import extract_emails_from_text, normalize_url

_PHONE_RE = re.compile(PHONE_PATTERN)
_PHONE_JUNK_RE = re.compile(r"^[\d\s().+\-]{7,25}$")


def extract_phones_from_text(text: str) -> set[str]:
    if not text:
        return set()
    found: set[str] = set()
    for match in _PHONE_RE.finditer(text):
        raw = match.group(0).strip()
        digits = re.sub(r"\D", "", raw)
        if len(digits) < 10 or len(digits) > 15:
            continue
        if not _PHONE_JUNK_RE.match(raw):
            continue
        normalized = _normalize_phone(raw, digits)
        found.add(normalized)
    return found


def _normalize_phone(raw: str, digits: str) -> str:
    if len(digits) == 11 and digits.startswith("1"):
        return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    if len(digits) == 10:
        return f"({digits[0:3]}) {digits[3:6]}-{digits[6:]}"
    return raw.strip()


def extract_phones_from_html(html: str) -> set[str]:
    phones = extract_phones_from_text(html)
    for match in re.finditer(r'href=["\']tel:([^"\']+)["\']', html, re.I):
        tel = unescape(match.group(1)).strip()
        digits = re.sub(r"\D", "", tel)
        if len(digits) >= 10:
            phones.add(_normalize_phone(tel, digits))
    return phones


_TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.I | re.S)
_OG_SITE_RE = re.compile(
    r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)
_OG_SITE_RE_ALT = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:site_name["\']',
    re.I,
)
_OG_TITLE_RE = re.compile(
    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)
_H1_RE = re.compile(r"<h1[^>]*>([^<]+)</h1>", re.I | re.S)
_NAME_MAX_LEN = 120


def _clean_name(value: str) -> str:
    text = unescape(re.sub(r"\s+", " ", value)).strip()
    for sep in ("|", "–", "-", "—", "·"):
        if sep in text:
            text = text.split(sep)[0].strip()
    if len(text) > _NAME_MAX_LEN:
        text = text[:_NAME_MAX_LEN].rsplit(" ", 1)[0]
    return text


def extract_business_name(html: str, page_url: str) -> str:
    candidates: list[str] = []

    for pattern in (_OG_SITE_RE, _OG_SITE_RE_ALT, _OG_TITLE_RE):
        m = pattern.search(html)
        if m:
            candidates.append(_clean_name(m.group(1)))

    for block in _extract_json_ld_blocks(html):
        name = _name_from_json_ld(block)
        if name:
            candidates.append(_clean_name(name))

    m = _H1_RE.search(html)
    if m:
        candidates.append(_clean_name(re.sub(r"<[^>]+>", "", m.group(1))))

    m = _TITLE_RE.search(html)
    if m:
        candidates.append(_clean_name(m.group(1)))

    for candidate in candidates:
        if candidate and len(candidate) >= 2:
            return candidate

    host = urlparse(page_url).netloc
    if host.startswith("www."):
        host = host[4:]
    return host.split(".")[0].replace("-", " ").title()


_STREET_RE = re.compile(
    r"\d{1,6}\s+[\w\s.'#-]{2,60}"
    r"(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Boulevard|Blvd\.?|"
    r"Drive|Dr\.?|Lane|Ln\.?|Way|Court|Ct\.?|Place|Pl\.?|Parkway|Pkwy\.?)"
    r"[^<\n]{0,80}"
    r"(?:[A-Z]{2}\s+\d{5}(?:-\d{4})?|\d{5}(?:-\d{4})?)",
    re.I,
)


def extract_addresses(html: str) -> set[str]:
    addresses: set[str] = set()

    for block in _extract_json_ld_blocks(html):
        for addr in _addresses_from_json_ld(block):
            if addr:
                addresses.add(addr)

    for match in _STREET_RE.finditer(html):
        addr = re.sub(r"\s+", " ", match.group(0)).strip()
        if 15 <= len(addr) <= 200:
            addresses.add(addr)

    for match in re.finditer(
        r'itemprop=["\']streetAddress["\'][^>]*>([^<]+)<',
        html,
        re.I,
    ):
        street = unescape(match.group(1)).strip()
        city_m = re.search(
            r'itemprop=["\']addressLocality["\'][^>]*>([^<]+)<',
            html[match.end() : match.end() + 500],
            re.I,
        )
        region_m = re.search(
            r'itemprop=["\']addressRegion["\'][^>]*>([^<]+)<',
            html[match.end() : match.end() + 500],
            re.I,
        )
        postal_m = re.search(
            r'itemprop=["\']postalCode["\'][^>]*>([^<]+)<',
            html[match.end() : match.end() + 500],
            re.I,
        )
        parts = [street]
        if city_m:
            parts.append(city_m.group(1).strip())
        if region_m:
            parts.append(region_m.group(1).strip())
        if postal_m:
            parts.append(postal_m.group(1).strip())
        addresses.add(", ".join(parts))

    return addresses


def extract_contact_urls(html: str, page_url: str) -> set[str]:
    urls: set[str] = set()
    base = page_url

    for match in re.finditer(r'href=["\']([^"\']+)["\']', html, re.I):
        href = unescape(match.group(1)).strip()
        lower = href.lower()
        if lower.startswith("mailto:") or lower.startswith("tel:"):
            continue
        absolute = normalize_url(href, base=base)
        if not absolute:
            continue
        path_lower = urlparse(absolute).path.lower()
        if any(kw in path_lower for kw in CONTACT_PATH_KEYWORDS):
            urls.add(absolute)

    return urls


def extract_social_urls(html: str, page_url: str) -> set[str]:
    urls: set[str] = set()
    for match in re.finditer(r'href=["\']([^"\']+)["\']', html, re.I):
        href = unescape(match.group(1)).strip()
        absolute = normalize_url(href, base=page_url)
        if not absolute:
            continue
        host = urlparse(absolute).netloc.lower()
        if any(fragment in host for fragment in SOCIAL_HOST_FRAGMENTS):
            urls.add(absolute)
    return urls


def extract_mailto_emails(html: str) -> set[str]:
    emails: set[str] = set()
    for match in re.finditer(r'href=["\']mailto:([^"\'?]+)', html, re.I):
        addr = unescape(match.group(1)).strip().lower()
        if "@" in addr:
            emails.add(addr)
    return emails


def extract_restaurant_hints(html: str) -> dict[str, set[str]]:
    extra: dict[str, set[str]] = {}
    lower = html.lower()

    if any(kw in lower for kw in RESTAURANT_KEYWORDS):
        extra.setdefault("tags", set()).add("restaurant")

    for block in _extract_json_ld_blocks(html):
        types = block.get("@type")
        if isinstance(types, str):
            types = [types]
        if isinstance(types, list):
            for t in types:
                if t in (
                    "Restaurant",
                    "FoodEstablishment",
                    "LocalBusiness",
                    "Organization",
                ):
                    extra.setdefault("tags", set()).add("restaurant")
                    break
        cuisine = block.get("servesCuisine")
        if cuisine:
            if isinstance(cuisine, str):
                extra.setdefault("cuisine", set()).add(cuisine)
            elif isinstance(cuisine, list):
                extra["cuisine"] = extra.get("cuisine", set()) | {
                    str(c) for c in cuisine
                }

    for key in CUISINE_META_KEYS:
        pattern = rf'<meta[^>]+name=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']'
        m = re.search(pattern, html, re.I)
        if m:
            extra.setdefault("cuisine", set()).add(unescape(m.group(1)).strip())

    return extra


def extract_page_data(html: str, page_url: str) -> dict[str, Any]:
    """Run all extractors on one page; safe on malformed HTML."""
    try:
        emails = extract_emails_from_text(html)
        emails.update(extract_mailto_emails(html))

        phones = extract_phones_from_html(html)
        addresses = extract_addresses(html)
        contact_urls = extract_contact_urls(html, page_url)
        social_urls = extract_social_urls(html, page_url)
        business_name = extract_business_name(html, page_url)
        extra = extract_restaurant_hints(html)
        restaurants = extract_individual_restaurants(html, page_url)

        return {
            "emails": emails,
            "phones": phones,
            "addresses": addresses,
            "contact_urls": contact_urls,
            "social_urls": social_urls,
            "business_name": business_name,
            "extra": extra,
            "restaurants": restaurants,
            "source_url": page_url,
        }
    except Exception as exc:
        print(f"[extract] Warning on {page_url}: {exc}")
        return {
            "emails": set(),
            "phones": set(),
            "addresses": set(),
            "contact_urls": set(),
            "social_urls": set(),
            "business_name": "",
            "extra": {},
            "restaurants": [],
            "source_url": page_url,
        }


# ---------------------------------------------------------------------------
# Per-restaurant (structured) extraction
# ---------------------------------------------------------------------------

_BUSINESS_LD_TYPES = {
    "Restaurant",
    "FoodEstablishment",
    "LocalBusiness",
    "Store",
}


def _unescape_json_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return unescape(value.replace("\\/", "/"))


def _first_str(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list) and value:
        return _first_str(value[0])
    return ""


def _phone_from_value(value: Any) -> str:
    raw = _first_str(value)
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 10:
        return ""
    return _normalize_phone(raw, digits)


def _social_from_same_as(value: Any, page_url: str) -> set[str]:
    urls: set[str] = set()
    items = value if isinstance(value, list) else [value]
    for item in items:
        if not item:
            continue
        absolute = normalize_url(str(item).strip(), base=page_url)
        if not absolute:
            continue
        host = urlparse(absolute).netloc.lower()
        if any(fragment in host for fragment in SOCIAL_HOST_FRAGMENTS):
            urls.add(absolute)
    return urls


def _record_from_json_ld(block: dict[str, Any], page_url: str) -> dict[str, Any] | None:
    types = block.get("@type")
    if isinstance(types, str):
        types = [types]
    if types and not any(t in _BUSINESS_LD_TYPES for t in types):
        return None

    name = _clean_name(_first_str(block.get("name")))
    if not name or len(name) < 2:
        return None

    phone = _phone_from_value(block.get("telephone") or block.get("phone"))
    email = _first_str(block.get("email")).lower()
    addrs = _addresses_from_json_ld(block)
    address = addrs[0] if addrs else ""
    social_urls = _social_from_same_as(block.get("sameAs"), page_url)

    if not phone and not email and not social_urls:
        return None

    return {
        "name": name,
        "email": email,
        "phone": phone,
        "address": address,
        "social_urls": social_urls,
        "source_url": page_url,
    }


def _extract_embedded_listing_records(html: str, page_url: str) -> list[dict[str, Any]]:
    """Parse inline JSON blobs with name + phone (common on directory sites)."""
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for match in re.finditer(r'"name"\s*:\s*"((?:[^"\\]|\\.)+)"', html):
        name = _clean_name(_unescape_json_string(match.group(1)))
        if len(name) < 2 or len(name) > _NAME_MAX_LEN:
            continue

        window = html[match.end() : match.end() + 1200]
        phone_m = re.search(r'"phone"\s*:\s*"((?:[^"\\]|\\.)*)"', window)
        if not phone_m:
            continue

        phone = _phone_from_value(_unescape_json_string(phone_m.group(1)))
        if not phone:
            continue

        email = ""
        email_m = re.search(r'"email"\s*:\s*"((?:[^"\\]|\\.)*)"', window)
        if email_m:
            email = _unescape_json_string(email_m.group(1)).lower().strip()

        address = ""
        addr_m = re.search(r'"address"\s*:\s*"((?:[^"\\]|\\.)*)"', window)
        if addr_m:
            address = _unescape_json_string(addr_m.group(1)).strip()
            if len(address) > 200 or "\\" in address[:20]:
                address = ""

        social_urls: set[str] = set()
        for social_m in re.finditer(
            r'"(?:instagram|facebook|twitter|social)[^"]*"\s*:\s*"((?:[^"\\]|\\.)*)"',
            window,
            re.I,
        ):
            url = normalize_url(_unescape_json_string(social_m.group(1)), base=page_url)
            if url:
                social_urls.add(url)

        key = (name.lower(), email, re.sub(r"\D", "", phone))
        if key in seen:
            continue
        seen.add(key)

        records.append(
            {
                "name": name,
                "email": email,
                "phone": phone,
                "address": address,
                "social_urls": social_urls,
                "source_url": page_url,
            }
        )

    return records


def extract_individual_restaurants(html: str, page_url: str) -> list[dict[str, Any]]:
    """Return one dict per restaurant/business found on the page."""
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def _add(record: dict[str, Any] | None) -> None:
        if not record:
            return
        key = (
            str(record.get("name", "")).lower().strip(),
            str(record.get("email", "")).lower().strip(),
            re.sub(r"\D", "", str(record.get("phone", ""))),
        )
        if key in seen:
            return
        seen.add(key)
        records.append(record)

    for block in _extract_json_ld_blocks(html):
        _add(_record_from_json_ld(block, page_url))

    for record in _extract_embedded_listing_records(html, page_url):
        _add(record)

    return records


def _extract_json_ld_blocks(html: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.I | re.S,
    ):
        raw = match.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        blocks.extend(_flatten_ld(data))
    return blocks


def _flatten_ld(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        out: list[dict[str, Any]] = []
        for item in data:
            out.extend(_flatten_ld(item))
        return out
    if isinstance(data, dict):
        if "@graph" in data:
            return _flatten_ld(data["@graph"])
        return [data]
    return []


def _name_from_json_ld(block: dict[str, Any]) -> str:
    types = block.get("@type")
    if isinstance(types, str):
        types = [types]
    interesting = {
        "Restaurant",
        "FoodEstablishment",
        "LocalBusiness",
        "Organization",
        "Store",
    }
    if types and not any(t in interesting for t in types):
        return ""
    name = block.get("name")
    return str(name).strip() if name else ""


def _addresses_from_json_ld(block: dict[str, Any]) -> list[str]:
    results: list[str] = []
    addr = block.get("address")
    if isinstance(addr, str) and addr.strip():
        results.append(addr.strip())
    elif isinstance(addr, dict):
        formatted = _format_postal_address(addr)
        if formatted:
            results.append(formatted)
    elif isinstance(addr, list):
        for item in addr:
            if isinstance(item, str):
                results.append(item.strip())
            elif isinstance(item, dict):
                formatted = _format_postal_address(item)
                if formatted:
                    results.append(formatted)
    return results


def _format_postal_address(addr: dict[str, Any]) -> str:
    parts = []
    for key in (
        "streetAddress",
        "addressLocality",
        "addressRegion",
        "postalCode",
        "addressCountry",
    ):
        val = addr.get(key)
        if val:
            parts.append(str(val).strip())
    return ", ".join(parts) if parts else ""
