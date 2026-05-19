"""Logging, URL helpers, retries, and async utilities."""

from __future__ import annotations

import asyncio
import functools
import logging
import random
import re
import time
from pathlib import Path
from typing import Any, Callable, TypeVar
from urllib.parse import parse_qs, unquote, urljoin, urlparse, urlunparse

from config import (
    DDG_EXCLUDED_DOMAINS,
    EMAIL_PATTERN,
    LOG_FILE,
    MAX_DELAY_SEC,
    MIN_DELAY_SEC,
    PHONE_PATTERN,
    PROJECT_ROOT,
)

_EMAIL_RE = re.compile(EMAIL_PATTERN, re.IGNORECASE)
_PHONE_RE = re.compile(PHONE_PATTERN)

T = TypeVar("T")

_loggers: dict[str, logging.Logger] = {}


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure root logger with file + console handlers."""
    log_path = PROJECT_ROOT / LOG_FILE
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
        force=True,
    )
    logger = logging.getLogger("lead_scraper")
    logger.info("Logging initialized → %s", log_path)
    return logger


def get_logger(name: str) -> logging.Logger:
    if name not in _loggers:
        _loggers[name] = logging.getLogger(name)
    return _loggers[name]


def normalize_url(url: str, base: str | None = None) -> str | None:
    if not url or not str(url).strip():
        return None
    url = str(url).strip()
    if base:
        url = urljoin(base, url)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None
    netloc = parsed.netloc.lower()
    if not netloc:
        return None
    path = parsed.path or "/"
    return urlunparse((parsed.scheme.lower(), netloc, path, parsed.params, parsed.query, ""))


def extract_ddg_target(href: str) -> str | None:
    if not href:
        return None
    parsed = urlparse(href)
    host = parsed.netloc.lower()
    if any(domain in host for domain in DDG_EXCLUDED_DOMAINS):
        if parsed.path.rstrip("/") in ("/l", "/y.js"):
            uddg = parse_qs(parsed.query).get("uddg", [None])[0]
            if uddg:
                return normalize_url(unquote(uddg))
        return None
    return normalize_url(href)


def get_registrable_domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def is_external_site(url: str) -> bool:
    domain = get_registrable_domain(url)
    return not any(excluded in domain for excluded in DDG_EXCLUDED_DOMAINS)


def is_junk_link(url: str) -> bool:
    from config import JUNK_PATH_FRAGMENTS

    lower = url.lower()
    return any(fragment in lower for fragment in JUNK_PATH_FRAGMENTS)


def is_same_domain(url: str, root_url: str) -> bool:
    return get_registrable_domain(url) == get_registrable_domain(root_url)


def extract_emails_from_text(text: str) -> set[str]:
    if not text:
        return set()
    found: set[str] = set()
    for match in _EMAIL_RE.finditer(text):
        email = match.group(0).lower().strip(".")
        if "@" in email and "." in email.split("@", 1)[1]:
            found.add(email)
    return found


def extract_phones_from_text(text: str) -> set[str]:
    if not text:
        return set()
    phones: set[str] = set()
    for match in _PHONE_RE.finditer(text):
        raw = match.group(0).strip()
        digits = re.sub(r"\D", "", raw)
        if 10 <= len(digits) <= 15:
            phones.add(raw)
    return phones


def extract_urls_from_text(text: str) -> list[str]:
    if not text:
        return []
    urls: set[str] = set()
    for match in re.finditer(r"https?://[\w\-./?&=%#]+", text, re.I):
        url = match.group(0).rstrip(".,;:)")
        urls.add(url)
    return sorted(urls)


def random_delay() -> float:
    return random.uniform(MIN_DELAY_SEC, MAX_DELAY_SEC)


async def async_random_delay() -> None:
    await asyncio.sleep(random_delay())


def retry_sync(
    attempts: int = 3,
    delay: float = 1.0,
    exceptions: tuple = (Exception,),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exc: Exception | None = None
            for attempt in range(1, attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt >= attempts:
                        raise
                    time.sleep(delay * attempt)
            raise last_exc  # type: ignore[misc]

        return wrapper

    return decorator


def retry_async(
    attempts: int = 3,
    delay: float = 1.0,
    exceptions: tuple = (Exception,),
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            for attempt in range(1, attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt >= attempts:
                        raise
                    await asyncio.sleep(delay * attempt)
            raise last_exc  # type: ignore[misc]

        return wrapper

    return decorator


def load_json_file(path: Path) -> dict[str, Any]:
    import json

    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_json_file(path: Path, data: Any) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
