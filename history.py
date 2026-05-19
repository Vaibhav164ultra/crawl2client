"""Track previously crawled sites so repeat runs skip them."""

from __future__ import annotations

import json
from pathlib import Path

from config import CRAWLED_HISTORY_FILE, OUTPUT_CSV, OUTPUT_JSON
from utils import get_registrable_domain, normalize_url


def _load_history_file(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    domains: set[str] = set()
    for item in data.get("domains", []):
        if item:
            domains.add(str(item).lower())
    return domains


def _load_domains_from_csv(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    try:
        import pandas as pd

        df = pd.read_csv(path)
    except Exception:
        return set()

    domains: set[str] = set()
    for column in ("source_website", "website"):
        if column not in df.columns:
            continue
        for value in df[column].dropna().astype(str):
            value = value.strip()
            if not value:
                continue
            normalized = normalize_url(value)
            if normalized:
                domains.add(get_registrable_domain(normalized).lower())
    return domains


def _load_domains_from_json(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else data.get("leads", [])
    except (json.JSONDecodeError, OSError):
        return set()

    domains: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("website", "source_website", "source_url"):
            value = str(item.get(key) or "").strip()
            if not value:
                continue
            normalized = normalize_url(value)
            if normalized:
                domains.add(get_registrable_domain(normalized).lower())
    return domains


def load_crawled_domains(
    history_path: str | Path = CRAWLED_HISTORY_FILE,
    csv_path: str | Path = OUTPUT_CSV,
    json_path: str | Path = OUTPUT_JSON,
) -> set[str]:
    """Domains already crawled in prior runs (history + CSV + JSON)."""
    domains = _load_history_file(Path(history_path))
    domains.update(_load_domains_from_csv(Path(csv_path)))
    domains.update(_load_domains_from_json(Path(json_path)))
    return domains


def domain_of(url: str) -> str:
    normalized = normalize_url(url)
    if not normalized:
        return ""
    return get_registrable_domain(normalized).lower()


def is_already_crawled(url: str, crawled_domains: set[str]) -> bool:
    domain = domain_of(url)
    return bool(domain and domain in crawled_domains)


def filter_new_urls(urls: list[str], crawled_domains: set[str]) -> list[str]:
    """Keep only URLs whose domain has not been crawled before."""
    fresh: list[str] = []
    seen_domains: set[str] = set()
    for url in urls:
        domain = domain_of(url)
        if not domain:
            continue
        if domain in crawled_domains or domain in seen_domains:
            continue
        seen_domains.add(domain)
        fresh.append(url)
    return fresh


def record_crawled_urls(
    urls: list[str],
    history_path: str | Path = CRAWLED_HISTORY_FILE,
    csv_path: str | Path = OUTPUT_CSV,
) -> None:
    """Persist newly crawled domains for future runs."""
    domains = load_crawled_domains(history_path, csv_path)
    for url in urls:
        domain = domain_of(url)
        if domain:
            domains.add(domain)

    path = Path(history_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "domains": sorted(domains),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[history] Recorded {len(domains)} crawled domain(s) in {path}")
