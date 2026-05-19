"""Per-site BFS crawler with isolated Playwright browser per worker."""

from __future__ import annotations

from collections import deque
from typing import Deque

from playwright.sync_api import sync_playwright

from config import (
    BROWSER_LAUNCH_ARGS,
    BROWSER_TYPE,
    MAX_INTERNAL_LINKS_PER_PAGE,
    NAVIGATION_TIMEOUT_MS,
    PAGE_LOAD_RETRIES,
    PAGE_LOAD_WAIT,
    RETRY_PAUSE_MS,
    STEALTH_INIT_SCRIPT,
    USER_AGENT,
)
from extractors import extract_page_data
from models import SiteLead
from utils import (
    get_registrable_domain,
    is_junk_link,
    is_same_domain,
    normalize_url,
)


def _launch_browser(playwright, headless: bool):
    launcher = getattr(playwright, BROWSER_TYPE)
    return launcher.launch(headless=headless, args=BROWSER_LAUNCH_ARGS)


def _goto_page(page, url: str) -> None:
    last_error: Exception | None = None
    for attempt in range(1, PAGE_LOAD_RETRIES + 1):
        try:
            page.goto(
                url,
                wait_until=PAGE_LOAD_WAIT,
                timeout=NAVIGATION_TIMEOUT_MS,
            )
            return
        except Exception as exc:
            last_error = exc
            if attempt >= PAGE_LOAD_RETRIES:
                raise
            print(f"[crawler] Retry {attempt}/{PAGE_LOAD_RETRIES} for {url}: {exc}")
            page.wait_for_timeout(RETRY_PAUSE_MS)
    if last_error:
        raise last_error


def crawl_site(start_url: str, max_depth: int, headless: bool) -> SiteLead:
    """
    Deep-crawl *start_url* up to *max_depth* using BFS.
    Each call creates its own browser — safe for thread pools.
    """
    root = normalize_url(start_url)
    if not root:
        print(f"[crawler] Invalid start URL: {start_url!r}")
        return SiteLead(website=start_url or "")

    domain_label = get_registrable_domain(root)
    print(f"[crawler] Starting {root} (depth={max_depth}, domain={domain_label})")

    lead = SiteLead(website=root)
    visited: set[str] = set()
    enqueued: set[str] = {root}
    queue: Deque[tuple[str, int]] = deque([(root, 0)])

    with sync_playwright() as playwright:
        browser = _launch_browser(playwright, headless)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 900},
        )
        context.add_init_script(STEALTH_INIT_SCRIPT)
        page = context.new_page()

        try:
            while queue:
                url, depth = queue.popleft()
                if url in visited:
                    continue
                visited.add(url)

                try:
                    _goto_page(page, url)
                    lead.pages_crawled += 1
                    html = page.content()
                    page_data = extract_page_data(html, url)
                    lead.merge_page(page_data)

                    print(
                        f"[crawler] {domain_label} | depth={depth} | "
                        f"page {lead.pages_crawled} | "
                        f"restaurants={len(lead.restaurants)}"
                    )

                    if depth >= max_depth:
                        continue

                    internal = _extract_internal_links(page, root)
                    added = 0
                    for link in internal:
                        if link in visited or link in enqueued:
                            continue
                        if is_junk_link(link):
                            continue
                        enqueued.add(link)
                        queue.append((link, depth + 1))
                        added += 1
                        if added >= MAX_INTERNAL_LINKS_PER_PAGE:
                            break

                except Exception as exc:
                    print(f"[crawler] Skip {url}: {exc}")

        finally:
            page.close()
            context.close()
            browser.close()

    print(
        f"[crawler] Done {root} | pages={lead.pages_crawled} | "
        f"restaurants={len(lead.restaurants)} | "
        f"name={lead.business_name!r}"
    )
    return lead


def _extract_internal_links(page, root_url: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    try:
        anchors = page.query_selector_all("a[href]")
    except Exception:
        return links

    for anchor in anchors:
        href = anchor.get_attribute("href")
        if not href:
            continue
        absolute = normalize_url(href, base=root_url)
        if not absolute:
            continue
        if not is_same_domain(absolute, root_url):
            continue
        if is_junk_link(absolute):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        links.append(absolute)

    return links
