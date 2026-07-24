#!/usr/bin/env python3
"""Live canary for configured Taiwan primary-record RSS sources."""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import feedparser
import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.cleaner import extract_markdown


CONFIG_PATH = ROOT / "config" / "config.yaml"


def configured_sources(path: Path = CONFIG_PATH) -> list[dict[str, Any]]:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    thresholds = config.get("filters", {}).get("min_word_count", {})
    sources = []
    for row in config.get("feeds", []):
        if "primary-record" not in (row.get("tags") or []):
            continue
        source = dict(row)
        source_type = str(source.get("source_type") or "article")
        if isinstance(source.get("min_word_count"), int):
            threshold = int(source["min_word_count"])
        elif isinstance(thresholds, dict):
            threshold = int(
                thresholds.get(source_type, thresholds.get("default", 100))
            )
        else:
            threshold = int(thresholds or 100)
        source["effective_min_word_count"] = threshold
        sources.append(source)
    return sources


def _plain_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def _runtime_word_count(value: str) -> int:
    compact = "".join((value or "").split())
    return max(1, len(compact) // 3) if compact else 0


def _same_site(source_url: str, entry_url: str) -> bool:
    def host(value: str) -> str:
        name = (urlparse(value).hostname or "").lower()
        return name[4:] if name.startswith("www.") else name

    source_host = host(source_url)
    entry_host = host(entry_url)
    return bool(
        source_host
        and entry_host
        and (
            source_host == entry_host
            or entry_host.endswith(f".{source_host}")
        )
    )


def validate_payload(
    source: dict[str, Any],
    *,
    status_code: int,
    content_type: str,
    payload: bytes,
) -> dict[str, Any]:
    parsed = feedparser.parse(payload)
    errors: list[str] = []
    if status_code != 200:
        errors.append(f"http_{status_code}")
    if parsed.bozo:
        errors.append(f"parse_error:{type(parsed.bozo_exception).__name__}")
    entries = list(parsed.entries)
    if not entries:
        errors.append("no_entries")
    sampled = entries[:3]
    for index, entry in enumerate(sampled):
        if not str(entry.get("title") or "").strip():
            errors.append(f"entry_{index}_missing_title")
        if not str(entry.get("link") or "").strip():
            errors.append(f"entry_{index}_missing_link")
        if not str(entry.get("published") or entry.get("updated") or "").strip():
            errors.append(f"entry_{index}_missing_timestamp")
        link = str(entry.get("link") or "").strip()
        if link and not _same_site(str(source.get("url") or ""), link):
            errors.append(f"entry_{index}_offsite_link")
    latest_link = str(entries[0].get("link") or "").strip() if entries else ""
    summary_length = 0
    summary_word_count = 0
    if entries:
        summary = _plain_text(
            entries[0].get("summary") or entries[0].get("description") or ""
        )
        summary_length = len(summary)
        summary_word_count = _runtime_word_count(
            f"{entries[0].get('title') or ''}\n{summary}"
        )
        threshold = int(
            source.get("effective_min_word_count")
            or source.get("min_word_count")
            or 100
        )
        if (
            source.get("source_type") == "rss_summary"
            and summary_word_count < threshold
        ):
            errors.append(f"summary_too_short:{summary_word_count}<{threshold}")
    return {
        "name": source.get("name"),
        "url": source.get("url"),
        "ok": not errors,
        "http_status": status_code,
        "content_type": content_type,
        "entries": len(entries),
        "latest_title": str(entries[0].get("title") or "")[:120] if entries else "",
        "latest_timestamp": str(
            entries[0].get("published") or entries[0].get("updated") or ""
        )[:80] if entries else "",
        "latest_link": latest_link,
        "latest_summary_length": summary_length,
        "latest_summary_word_count": summary_word_count,
        "article_http_status": None,
        "article_word_count": None,
        "errors": errors,
    }


async def check_source(client: httpx.AsyncClient, source: dict[str, Any]) -> dict[str, Any]:
    try:
        response = await client.get(source["url"])
    except httpx.HTTPError as exc:
        return {
            "name": source.get("name"),
            "url": source.get("url"),
            "ok": False,
            "errors": [f"request_error:{type(exc).__name__}"],
        }
    result = validate_payload(
        source,
        status_code=response.status_code,
        content_type=response.headers.get("content-type", ""),
        payload=response.content,
    )
    if source.get("source_type") != "article" or not result.get("latest_link"):
        return result

    try:
        article_response = await client.get(str(result["latest_link"]))
    except httpx.HTTPError as exc:
        result["errors"].append(f"article_request_error:{type(exc).__name__}")
        result["ok"] = False
        return result

    result["article_http_status"] = article_response.status_code
    if article_response.status_code != 200:
        result["errors"].append(f"article_http_{article_response.status_code}")
    _, article_word_count = extract_markdown(article_response.text)
    result["article_word_count"] = article_word_count
    threshold = int(
        source.get("effective_min_word_count")
        or source.get("min_word_count")
        or 100
    )
    if article_word_count < threshold:
        result["errors"].append(
            f"article_too_short:{article_word_count}<{threshold}"
        )
    result["ok"] = not result["errors"]
    return result


async def run(report_path: Path) -> int:
    sources = configured_sources()
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=60.0,
        headers={"User-Agent": "News-Radar-Recovery-Source-Canary/1.0"},
    ) as client:
        results = []
        for source in sources:
            result = await check_source(client, source)
            results.append(result)
            marker = "PASS" if result["ok"] else "FAIL"
            print(
                f"[{marker}] {result['name']} entries={result.get('entries', 0)} "
                f"summary_words={result.get('latest_summary_word_count', 0)} "
                f"article_words={result.get('article_word_count')} "
                f"errors={','.join(result.get('errors') or []) or 'none'}"
            )
    report = {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "sources_checked": len(results),
        "passed": sum(1 for result in results if result["ok"]),
        "failed": sum(1 for result in results if not result["ok"]),
        "results": results,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 1 if report["failed"] else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--report", default="reports/recovery_source_canary.json"
    )
    args = parser.parse_args()
    return asyncio.run(run(Path(args.report)))


if __name__ == "__main__":
    raise SystemExit(main())
