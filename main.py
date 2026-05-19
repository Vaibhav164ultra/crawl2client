#!/usr/bin/env python3
"""AI-powered lead generation pipeline — CLI entry point."""

from __future__ import annotations

import argparse
import asyncio
import sys

from config import (
    DEFAULT_CONCURRENCY,
    MAX_DEPTH_DEFAULT,
    MAX_SITES_DEFAULT,
    MIN_CONFIDENCE,
    OLLAMA_MODEL,
)
from deps import check_dependencies
from pipeline import run_pipeline
from utils import setup_logging


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "AI-powered lead scraper. "
            "Run with NO arguments to type your query interactively, "
            "or use -q \"your search\" to pass it on the command line."
        ),
        epilog=(
            "Examples:\n"
            "  python main.py                          # prompts for your query\n"
            "  python main.py -q \"plumbers mumbai\" -n 5\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-q",
        "--query",
        help="Search query (if omitted, you will be prompted to type one)",
    )
    parser.add_argument(
        "-n",
        "--max-sites",
        type=int,
        default=MAX_SITES_DEFAULT,
        help=f"Max sites to crawl (default: {MAX_SITES_DEFAULT})",
    )
    parser.add_argument(
        "-d",
        "--depth",
        type=int,
        default=MAX_DEPTH_DEFAULT,
        help=f"Crawl depth per site (default: {MAX_DEPTH_DEFAULT})",
    )
    parser.add_argument("--headless", action="store_true", help="Headless browser")
    parser.add_argument("--show-browser", action="store_true", help="Show browser window")
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"Parallel site workers (default: {DEFAULT_CONCURRENCY})",
    )
    parser.add_argument(
        "--skip-crawled",
        action="store_true",
        help="Skip URLs from previously crawled domains (useful for repeat runs)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Deprecated: all URLs are crawled by default; use --skip-crawled to enable history-based skipping.",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=MIN_CONFIDENCE,
        help=f"Drop leads below this confidence (default: {MIN_CONFIDENCE})",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Disable Ollama; use regex/heuristic extraction only",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    return parser.parse_args(argv)


def _prompt() -> tuple[str, int, int, bool]:
    print("=== AI Lead Scraper ===\n")
    query = input("Search query: ").strip()
    while not query:
        query = input("Search query: ").strip()
        
    crawl_mode = input("Crawl limit: 'limited' (e.g. 5 sites) or 'unlimited' (as many as possible)? [limited]: ").strip().lower()
    if crawl_mode in ("unlimited", "u", "all", "max"):
        max_sites = 9999
        print(f"Max sites set to unlimited.")
    else:
        raw_max = input(f"Max sites [{MAX_SITES_DEFAULT}]: ").strip() or str(MAX_SITES_DEFAULT)
        max_sites = max(1, int(raw_max))
        
    raw_depth = input(f"Crawl depth [{MAX_DEPTH_DEFAULT}]: ").strip() or str(MAX_DEPTH_DEFAULT)
    depth = max(1, min(3, int(raw_depth)))
    show = input("Show browser? (y/n) [n]: ").strip().lower() in ("y", "yes", "1")
    return query, max_sites, depth, show


async def _async_main(args: argparse.Namespace) -> int:
    import logging

    setup_logging(logging.DEBUG if args.verbose else logging.INFO)

    if args.query:
        query = args.query.strip()
        max_sites = args.max_sites
        depth = args.depth
        if args.show_browser:
            headless = False
        else:
            headless = True
        print(
            "\n[main] Using query from command line (-q). "
            "To type your own query interactively, run: python main.py\n"
        )
    else:
        print(
            "\n[main] Interactive mode — enter your search below.\n"
            "(Tip: next time you can also run: python main.py -q \"your search\")\n"
        )
        query, max_sites, depth, show_browser = _prompt()
        headless = not show_browser

    use_ai = not args.no_ai

    mode = f"Ollama ({OLLAMA_MODEL})" if use_ai else "heuristics only"
    print(f"[main] Mode: {mode} | query={query!r} | sites={max_sites} | depth={depth}")

    try:
        skip_crawled = args.skip_crawled
        if args.force:
            skip_crawled = True

        current_query = query
        current_min_conf = args.min_confidence
        max_retries = 2
        leads = []

        for attempt in range(max_retries + 1):
            if attempt > 0:
                print(f"\n[main] Zero leads found. Retrying with modified query... (Attempt {attempt + 1}/{max_retries + 1})")
                # Modify query to improve chances
                current_query = f"{query} contact email".strip()
                # Reduce confidence threshold
                current_min_conf = max(0.1, current_min_conf - 0.15)
                print(f"[main] New query: {current_query!r} | New min_confidence: {current_min_conf:.2f}")

            leads = await run_pipeline(
                query=current_query,
                max_sites=max_sites,
                max_depth=depth,
                headless=headless,
                concurrency=args.workers,
                skip_crawled=skip_crawled,
                min_confidence=current_min_conf,
                use_ai=use_ai,
            )

            if leads:
                break
    except KeyboardInterrupt:
        print("\n[main] Interrupted.")
        return 130
    except Exception as exc:
        print(f"[main] Pipeline error: {exc}")
        import traceback

        traceback.print_exc()
        return 1

    print(f"\n[main] Done — {len(leads)} high-quality lead(s) exported.")
    for i, lead in enumerate(leads[:10], 1):
        print(
            f"  {i}. {lead.business_name} | {lead.email or '-'} | "
            f"{lead.phone or '-'} | score={lead.outreach_score:.1f}"
        )
    if len(leads) > 10:
        print(f"  ... and {len(leads) - 10} more in leads.json / leads_output.csv")
    return 0 if leads else 1


def main(argv: list[str] | None = None) -> int:
    if not check_dependencies():
        return 1
    args = _parse_args(argv)
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    sys.exit(main())
