"""DuckDuckGo search and external link extraction."""

from __future__ import annotations

import time
import traceback
from typing import TYPE_CHECKING

from playwright.sync_api import Page, sync_playwright

from config import (
    BROWSER_LAUNCH_ARGS,
    BROWSER_TYPE,
    DDG_CONSENT_SELECTORS,
    DDG_MORE_RESULTS_SELECTORS,
    DDG_RESULT_LINK_SELECTORS,
    DDG_RESULTS_CONTAINER,
    DDG_SEARCH_INPUT_SELECTORS,
    DDG_URL,
    MAX_SCROLL_ATTEMPTS,
    RETRY_PAUSE_MS,
    SCROLL_PAUSE_MS,
    SEARCH_RETRIES,
    SELECTOR_TIMEOUT_MS,
    STEALTH_INIT_SCRIPT,
    USER_AGENT,
)
from utils import extract_ddg_target, get_registrable_domain, is_external_site, normalize_url

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Playwright


def _launch_browser(playwright: Playwright, headless: bool) -> Browser:
    launcher = getattr(playwright, BROWSER_TYPE)
    return launcher.launch(
        headless=headless,
        args=BROWSER_LAUNCH_ARGS,
    )


def _new_context(browser: Browser) -> BrowserContext:
    context = browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1280, "height": 900},
        locale="en-US",
    )
    context.add_init_script(STEALTH_INIT_SCRIPT)
    return context


def _dismiss_consent(page: Page) -> None:
    """Click through cookie/consent banners if present."""
    for selector in DDG_CONSENT_SELECTORS:
        try:
            button = page.query_selector(selector)
            if button and button.is_visible():
                button.click(timeout=3000)
                page.wait_for_timeout(500)
                print("[search] Dismissed consent dialog.")
                return
        except Exception:
            continue


def _wait_for_search_input(page: Page):
    """Find the search box using fallback selectors."""
    last_error: Exception | None = None
    for selector in DDG_SEARCH_INPUT_SELECTORS:
        try:
            return page.wait_for_selector(
                selector,
                state="visible",
                timeout=SELECTOR_TIMEOUT_MS // len(DDG_SEARCH_INPUT_SELECTORS),
            )
        except Exception as exc:
            last_error = exc
    raise RuntimeError(
        "Could not find DuckDuckGo search input. "
        f"Tried: {DDG_SEARCH_INPUT_SELECTORS}. Last error: {last_error}"
    )


def _goto_with_retry(page: Page, url: str, label: str) -> None:
    for attempt in range(1, SEARCH_RETRIES + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=SELECTOR_TIMEOUT_MS)
            return
        except Exception as exc:
            if attempt >= SEARCH_RETRIES:
                raise RuntimeError(
                    f"{label} failed after {SEARCH_RETRIES} attempts: {exc}"
                ) from exc
            print(f"[search] {label} retry {attempt}/{SEARCH_RETRIES}: {exc}")
            page.wait_for_timeout(RETRY_PAUSE_MS)


def _collect_links_from_page(page: Page) -> list[str]:
    """Extract external result URLs using stable selectors and fallbacks."""
    links: list[str] = []
    seen: set[str] = set()

    for selector in DDG_RESULT_LINK_SELECTORS:
        try:
            anchors = page.query_selector_all(selector)
        except Exception:
            continue
        for anchor in anchors:
            href = anchor.get_attribute("href")
            if not href:
                continue
            target = extract_ddg_target(href) or normalize_url(href)
            if not target or not is_external_site(target):
                continue
            if target in seen:
                continue
            seen.add(target)
            links.append(target)

    if links:
        return links

    # Fallback: scan anchors inside the results region
    try:
        container = page.query_selector(DDG_RESULTS_CONTAINER.split(",")[0].strip())
        scope = container if container else page
        for anchor in scope.query_selector_all("a[href]"):
            href = anchor.get_attribute("href")
            if not href:
                continue
            target = extract_ddg_target(href) or normalize_url(href)
            if not target or not is_external_site(target):
                continue
            if target in seen:
                continue
            seen.add(target)
            links.append(target)
    except Exception as exc:
        print(f"[search] Fallback link scan failed: {exc}")

    return links


def _scroll_for_more(page: Page) -> bool:
    previous_height = page.evaluate("() => document.body.scrollHeight")
    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(SCROLL_PAUSE_MS)
    new_height = page.evaluate("() => document.body.scrollHeight")
    return new_height > previous_height


def _click_more_results(page: Page) -> bool:
    for selector in DDG_MORE_RESULTS_SELECTORS:
        try:
            button = page.query_selector(selector)
            if button and button.is_visible():
                button.click()
                page.wait_for_timeout(SCROLL_PAUSE_MS)
                return True
        except Exception:
            continue
    return False


def _wait_for_results(page: Page) -> None:
    """Wait until organic results or any result links appear."""
    containers = [s.strip() for s in DDG_RESULTS_CONTAINER.split(",")]
    last_error: Exception | None = None
    for selector in containers:
        try:
            page.wait_for_selector(
                selector,
                state="attached",
                timeout=SELECTOR_TIMEOUT_MS // max(len(containers), 1),
            )
            page.wait_for_timeout(1500)
            if _collect_links_from_page(page):
                return
        except Exception as exc:
            last_error = exc

    # Final wait: any external link on the page
    try:
        page.wait_for_function(
            """() => {
                const anchors = document.querySelectorAll('a[href]');
                for (const a of anchors) {
                    const h = a.getAttribute('href') || '';
                    if (h.includes('uddg=') || (h.startsWith('http') && !h.includes('duckduckgo')))
                        return true;
                }
                return false;
            }""",
            timeout=SELECTOR_TIMEOUT_MS,
        )
    except Exception as exc:
        raise RuntimeError(
            "DuckDuckGo results did not load. "
            f"Try --show-browser to inspect the page. Last error: {last_error or exc}"
        ) from exc


def search_duckduckgo(
    query: str,
    max_sites: int,
    headless: bool,
    exclude_domains: set[str] | None = None,
) -> list[str]:
    """
    Open DuckDuckGo, run *query*, collect up to *max_sites* unique external URLs.
    Uses its own Playwright browser instance (call from main thread only).
    """
    query = (query or "").strip()
    if not query:
        raise ValueError("Search query cannot be empty.")

    exclude_domains = {d.lower() for d in (exclude_domains or set())}
    print(
        f"[search] Query: {query!r} | max_sites={max_sites} | "
        f"headless={headless} | excluding {len(exclude_domains)} domain(s)"
    )

    collected: list[str] = []
    seen: set[str] = set()
    seen_domains: set[str] = set()

    try:
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright, headless)
            context = _new_context(browser)
            page = context.new_page()
            try:
                _goto_with_retry(page, DDG_URL, "Open DuckDuckGo")
                _dismiss_consent(page)

                search_input = _wait_for_search_input(page)
                search_input.click()
                search_input.fill(query)
                search_input.press("Enter")

                _wait_for_results(page)

                scroll_round = 0
                while len(collected) < max_sites and scroll_round < MAX_SCROLL_ATTEMPTS:
                    batch = _collect_links_from_page(page)
                    skipped = 0
                    for link in batch:
                        if link in seen:
                            continue
                        seen.add(link)
                        domain = get_registrable_domain(link).lower()
                        if domain in exclude_domains or domain in seen_domains:
                            skipped += 1
                            continue
                        seen_domains.add(domain)
                        collected.append(link)
                        if len(collected) >= max_sites:
                            break

                    print(
                        f"[search] Round {scroll_round + 1}: "
                        f"{len(collected)} new site(s)"
                        + (f" ({skipped} skipped as already crawled)" if skipped else "")
                    )
                    if len(collected) >= max_sites:
                        break

                    clicked_more = _click_more_results(page)
                    scrolled = _scroll_for_more(page)
                    if not clicked_more and not scrolled:
                        print("[search] No more results to load.")
                        break
                    scroll_round += 1
                    time.sleep(0.5)

            finally:
                context.close()
                browser.close()

    except Exception as exc:
        print(f"[search] Error during DuckDuckGo search: {exc}")
        traceback.print_exc()

    result = collected[:max_sites]
    print(f"[search] Returning {len(result)} site(s).")
    if not result:
        print(
            "[search] Tip: run with --show-browser, verify playwright install, "
            "or try a simpler query."
        )
    return result
