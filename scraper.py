"""Async Playwright scraper: search discovery + site crawling with URL cache."""

from __future__ import annotations

import asyncio
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Deque

from playwright.async_api import Browser, Page, async_playwright

from ai_agent import AIAgent
from config import (
    BING_RESULT_LINK_SELECTORS,
    BING_SEARCH_INPUT,
    BING_URL,
    BROWSER_LAUNCH_ARGS,
    BROWSER_TYPE,
    DDG_EXCLUDED_DOMAINS,
    DDG_RESULT_LINK_SELECTORS,
    DDG_SEARCH_INPUT_SELECTORS,
    DDG_URL,
    GOOGLE_RESULT_LINK_SELECTORS,
    GOOGLE_SEARCH_INPUT,
    GOOGLE_URL,
    MAX_DEPTH_DEFAULT,
    MAX_INTERNAL_LINKS_PER_PAGE,
    MAX_PAGES_PER_SITE,
    MAX_SCROLL_ATTEMPTS,
    NAVIGATION_TIMEOUT_MS,
    PAGE_LOAD_RETRIES,
    PAGE_LOAD_WAIT,
    RETRY_PAUSE_MS,
    SCROLL_PAUSE_MS,
    SEARCH_ENGINES,
    SEARCH_RETRIES,
    SELECTOR_TIMEOUT_MS,
    STEALTH_INIT_SCRIPT,
    USER_AGENTS,
    VISITED_CACHE_FILE,
)
from models import ScrapedPage
from utils import (
    async_random_delay,
    extract_ddg_target,
    get_logger,
    get_registrable_domain,
    is_external_site,
    is_junk_link,
    is_same_domain,
    load_json_file,
    normalize_url,
    retry_async,
    save_json_file,
)
from config import PROJECT_ROOT  # noqa: E402

logger = get_logger(__name__)


@dataclass
class ScraperConfig:
    headless: bool = True
    max_depth: int = MAX_DEPTH_DEFAULT
    max_pages_per_site: int = MAX_PAGES_PER_SITE
    concurrency: int = 4


class VisitedCache:
    """Persist visited URLs across runs."""

    def __init__(self, path=PROJECT_ROOT / VISITED_CACHE_FILE, use_cache: bool = True) -> None:
        self.path = path
        self.use_cache = use_cache
        if use_cache:
            data = load_json_file(path)
            self._urls: set[str] = set(data.get("urls", []))
        else:
            self._urls: set[str] = set()

    def seen(self, url: str) -> bool:
        return url in self._urls

    def add(self, url: str) -> None:
        self._urls.add(url)

    def save(self) -> None:
        if self.use_cache:
            save_json_file(self.path, {"urls": sorted(self._urls)})


class AsyncLeadScraper:
    """Hybrid async scraper with stealth browser and parallel site processing."""

    def __init__(self, config: ScraperConfig | None = None, use_cache: bool = True) -> None:
        self.config = config or ScraperConfig()
        self.cache = VisitedCache(use_cache=use_cache)
        self._browser: Browser | None = None
        self._playwright = None

    async def __aenter__(self) -> AsyncLeadScraper:
        self._playwright = await async_playwright().start()
        launcher = getattr(self._playwright, BROWSER_TYPE)
        self._browser = await launcher.launch(
            headless=self.config.headless,
            args=BROWSER_LAUNCH_ARGS,
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self.cache.save()

    def _random_ua(self) -> str:
        return random.choice(USER_AGENTS)

    async def _new_page(self) -> Page:
        assert self._browser is not None
        context = await self._browser.new_context(
            user_agent=self._random_ua(),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        await context.add_init_script(STEALTH_INIT_SCRIPT)
        return await context.new_page()

    @retry_async(attempts=SEARCH_RETRIES, delay=1.5)
    async def _goto(self, page: Page, url: str) -> None:
        for attempt in range(1, PAGE_LOAD_RETRIES + 1):
            try:
                await page.goto(
                    url,
                    wait_until=PAGE_LOAD_WAIT,
                    timeout=NAVIGATION_TIMEOUT_MS,
                )
                return
            except Exception as exc:
                if attempt >= PAGE_LOAD_RETRIES:
                    raise
                logger.warning("Retry %d for %s: %s", attempt, url, exc)
                await page.wait_for_timeout(RETRY_PAUSE_MS)

    async def discover_urls(
        self,
        query: str,
        max_sites: int,
        exclude_domains: set[str] | None = None,
        agent: AIAgent | None = None,
    ) -> list[str]:
        """Search multiple engines and return unique external URLs filtered by AI."""
        exclude_domains = {d.lower() for d in (exclude_domains or set())}
        collected: list[str] = []
        seen_domains: set[str] = set()

        for engine in SEARCH_ENGINES:
            if len(collected) >= max_sites * 2:  # Collect more for AI to filter
                break
            try:
                batch = await self._search_engine(engine, query, max_sites * 2, exclude_domains, seen_domains)
                for url in batch:
                    domain = get_registrable_domain(url).lower()
                    if domain in exclude_domains or domain in seen_domains:
                        continue
                    seen_domains.add(domain)
                    collected.append(url)
                    if len(collected) >= max_sites * 2:
                        break
            except Exception as exc:
                logger.error("Search engine %s failed: %s", engine, exc)

        logger.info("Discovered %d URL(s) from search engines for query %r", len(collected), query)
        
        if agent and collected:
            logger.info("Using AI to select best links...")
            collected = agent.select_best_links(collected)
            
        return collected[:max_sites]

    async def _search_engine(
        self,
        engine: str,
        query: str,
        max_sites: int,
        exclude_domains: set[str],
        seen_domains: set[str],
    ) -> list[str]:
        page = await self._new_page()
        collected: list[str] = []
        seen_urls: set[str] = set()

        try:
            if engine == "google":
                await self._goto(page, GOOGLE_URL)
                inp = await page.wait_for_selector(GOOGLE_SEARCH_INPUT, timeout=SELECTOR_TIMEOUT_MS)
                link_selectors = GOOGLE_RESULT_LINK_SELECTORS
            elif engine == "bing":
                await self._goto(page, BING_URL)
                inp = await page.wait_for_selector(BING_SEARCH_INPUT, timeout=SELECTOR_TIMEOUT_MS)
                link_selectors = BING_RESULT_LINK_SELECTORS
            else:
                await self._goto(page, DDG_URL)
                inp = None
                for sel in DDG_SEARCH_INPUT_SELECTORS:
                    try:
                        inp = await page.wait_for_selector(sel, timeout=8000)
                        break
                    except Exception:
                        continue
                if not inp:
                    raise RuntimeError("DuckDuckGo search input not found")
                link_selectors = DDG_RESULT_LINK_SELECTORS

            await inp.click()
            await inp.fill(query)
            await inp.press("Enter")
            await page.wait_for_timeout(2500)

            for scroll_round in range(MAX_SCROLL_ATTEMPTS):
                links = await self._collect_result_links(page, link_selectors)
                for link in links:
                    if link in seen_urls:
                        continue
                    seen_urls.add(link)
                    domain = get_registrable_domain(link).lower()
                    if domain in exclude_domains or domain in seen_domains:
                        continue
                    collected.append(link)
                    if len(collected) >= max_sites:
                        return collected

                scrolled = await self._scroll_page(page)
                if not scrolled:
                    break
                await async_random_delay()

        finally:
            await page.context.close()

        return collected

    async def _collect_result_links(self, page: Page, selectors: list[str]) -> list[str]:
        links: list[str] = []
        seen: set[str] = set()
        for selector in selectors:
            try:
                anchors = await page.query_selector_all(selector)
            except Exception:
                continue
            for anchor in anchors:
                href = await anchor.get_attribute("href")
                if not href:
                    continue
                target = extract_ddg_target(href) or normalize_url(href)
                if not target or not is_external_site(target):
                    continue
                if target not in seen:
                    seen.add(target)
                    links.append(target)
        return links

    async def _scroll_page(self, page: Page) -> bool:
        prev = await page.evaluate("() => document.body.scrollHeight")
        await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(SCROLL_PAUSE_MS)
        new = await page.evaluate("() => document.body.scrollHeight")
        return new > prev

    async def scrape_site(self, start_url: str, agent: AIAgent | None = None) -> list[ScrapedPage]:
        """Crawl a site. If agent is provided, uses AI to guide navigation. Otherwise BFS."""
        root = normalize_url(start_url)
        if not root:
            return []

        if self.cache.seen(root):
            logger.debug("Skipping cached URL: %s", root)
            return []

        pages: list[ScrapedPage] = []
        visited: set[str] = set()
        queue: Deque[tuple[str, int]] = deque([(root, 0)])
        page = await self._new_page()

        try:
            while queue and len(pages) < self.config.max_pages_per_site:
                url, depth = queue.popleft()
                if url in visited or self.cache.seen(url):
                    continue
                visited.add(url)
                self.cache.add(url)

                try:
                    await async_random_delay()
                    await self._goto(page, url)
                    html = await page.content()
                    title = await page.title()
                    final = page.url
                    pages.append(
                        ScrapedPage(url=url, html=html, title=title, final_url=final)
                    )
                    logger.info("Scraped [%d] %s", len(pages), url)

                    if depth >= self.config.max_depth:
                        continue

                    links = await self._internal_links(page, root)
                    
                    if agent and agent.use_ai:
                        # AI guided navigation
                        try:
                            text = await page.evaluate("() => document.body.innerText")
                            nav_decision = agent.evaluate_page_navigation(text, links)
                            if nav_decision.get("has_contact_info"):
                                logger.info("AI found contact info on %s. Continuing exploration for more...", url)
                            
                            next_url = nav_decision.get("next_link")
                            if next_url and next_url not in visited and not self.cache.seen(next_url):
                                logger.info("AI suggested next link: %s", next_url)
                                queue.append((next_url, depth + 1))
                            elif links:
                                # Fallback to first unseen link
                                for link in links:
                                    if link not in visited and not self.cache.seen(link):
                                        queue.append((link, depth + 1))
                                        break
                        except Exception as exc:
                            logger.warning("AI navigation failed for %s: %s", url, exc)
                            # Fallback to BFS
                            for link in links[:3]:
                                if link not in visited and not self.cache.seen(link):
                                    queue.append((link, depth + 1))
                    else:
                        # Standard BFS
                        added = 0
                        for link in links:
                            if link not in visited and not self.cache.seen(link):
                                queue.append((link, depth + 1))
                                added += 1
                                if added >= MAX_INTERNAL_LINKS_PER_PAGE:
                                    break
                except Exception as exc:
                    logger.warning("Skip page %s: %s", url, exc)
        finally:
            await page.context.close()

        return pages

    async def _internal_links(self, page: Page, root_url: str) -> list[str]:
        links: list[str] = []
        seen: set[str] = set()
        try:
            anchors = await page.query_selector_all("a[href]")
        except Exception:
            return links
        for anchor in anchors:
            href = await anchor.get_attribute("href")
            if not href:
                continue
            absolute = normalize_url(href, base=root_url)
            if not absolute or not is_same_domain(absolute, root_url):
                continue
            if is_junk_link(absolute) or absolute in seen:
                continue
            seen.add(absolute)
            links.append(absolute)
        return links

    async def scrape_sites_parallel(
        self,
        urls: list[str],
        agent: AIAgent | None = None,
    ) -> dict[str, list[ScrapedPage]]:
        """Scrape multiple sites with bounded concurrency."""
        sem = asyncio.Semaphore(self.config.concurrency)
        results: dict[str, list[ScrapedPage]] = {}

        async def _worker(url: str) -> None:
            async with sem:
                try:
                    results[url] = await self.scrape_site(url, agent=agent)
                except Exception as exc:
                    logger.error("Site scrape failed %s: %s", url, exc)
                    results[url] = []

        await asyncio.gather(*[_worker(u) for u in urls])
        return results
