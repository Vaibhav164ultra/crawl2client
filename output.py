"""Human-readable TXT and structured CSV export — one row/block per restaurant."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from config import OUTPUT_COLUMNS, OUTPUT_CSV, OUTPUT_TXT
from models import RestaurantLead, SiteLead


def _join_sorted(values: Iterable[str]) -> str:
    return "; ".join(sorted(values))


def format_restaurant_block(index: int, lead: RestaurantLead) -> str:
    """Format a single restaurant as a structured block."""
    social = _join_sorted(lead.social_urls) if lead.social_urls else "(none)"
    lines = [
        f"--- Restaurant {index} ---",
        f"Name:    {lead.name or '(unknown)'}",
        f"Email:   {lead.email or '(none)'}",
        f"Phone:   {lead.phone or '(none)'}",
        f"Social:  {social}",
        f"Address: {lead.address or '(none)'}",
    ]
    if lead.source_url:
        lines.append(f"Page:    {lead.source_url}")
    return "\n".join(lines)


def format_site_block(site: SiteLead) -> str:
    """Format one crawled site with its individual restaurant records."""
    lines = [
        "=" * 80,
        f"SITE: {site.website}",
        f"Site Name: {site.business_name or '(unknown)'}",
        f"Pages Crawled: {site.pages_crawled}",
        f"Restaurants Found: {len(site.restaurants)}",
        "=" * 80,
        "",
    ]

    if not site.restaurants:
        lines.append("(no individual restaurant records extracted)")
        lines.append("")
    else:
        for i, restaurant in enumerate(site.restaurants, start=1):
            lines.append(format_restaurant_block(i, restaurant))
            lines.append("")

    return "\n".join(lines)


def save_txt(results: list[SiteLead], path: str = OUTPUT_TXT) -> None:
    total_restaurants = sum(len(s.restaurants) for s in results)
    new_blocks = (
        [format_site_block(site) for site in results]
        if results
        else ["(no new sites crawled this run)"]
    )

    out = Path(path)
    if out.is_file() and results:
        existing = out.read_text(encoding="utf-8").rstrip()
        content = existing + "\n\n" + "\n".join(new_blocks) + "\n"
    else:
        header = [
            "LEAD SCRAPER OUTPUT",
            f"Sites crawled this run: {len(results)}",
            f"Restaurants this run: {total_restaurants}",
            "",
        ]
        content = "\n".join(header) + "\n" + "\n".join(new_blocks) + "\n"

    out.write_text(content, encoding="utf-8")
    print(
        f"[output] Saved TXT ({len(results)} new site(s), "
        f"{total_restaurants} restaurant(s)) to {path}"
    )


def save_csv(results: list[SiteLead], path: str = OUTPUT_CSV) -> None:
    rows: list[dict[str, str | int]] = []
    for site in results:
        for lead in site.restaurants:
            rows.append(
                {
                    "source_website": site.website,
                    "restaurant_name": lead.name,
                    "email": lead.email,
                    "phone": lead.phone,
                    "address": lead.address,
                    "social": _join_sorted(lead.social_urls),
                    "source_page": lead.source_url,
                }
            )

    new_df = pd.DataFrame(rows, columns=list(OUTPUT_COLUMNS))
    out = Path(path)

    if new_df.empty:
        if not out.is_file():
            new_df = pd.DataFrame(
                [
                    {
                        "source_website": "",
                        "restaurant_name": "",
                        "email": "",
                        "phone": "",
                        "address": "",
                        "social": "",
                        "source_page": "",
                    }
                ],
                columns=list(OUTPUT_COLUMNS),
            )
            new_df.to_csv(out, index=False)
        print(f"[output] No new restaurant rows to add to {path}")
        return

    if out.is_file():
        try:
            existing = pd.read_csv(out)
            df = pd.concat([existing, new_df], ignore_index=True)
            df = df.drop_duplicates(
                subset=["source_website", "restaurant_name", "email", "phone"],
                keep="first",
            )
        except Exception:
            df = new_df
    else:
        df = new_df

    df.to_csv(out, index=False)
    print(f"[output] Saved CSV ({len(new_df)} new row(s), {len(df)} total) to {path}")


def save_results(results: list[SiteLead]) -> None:
    """Write both TXT and CSV outputs (always creates files)."""
    save_txt(results)
    save_csv(results)
