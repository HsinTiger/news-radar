"""Evidence-pack builder for deep Substack articles.

Podcast and company drafts use this module between the source-digest call and
the final writer call.  Search results do not become evidence merely because a
URL exists: each accepted source needs a readable excerpt, a unique URL, and a
clear publisher.  Fewer than five usable extension sources fails closed.
"""

from __future__ import annotations

import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, Literal, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field, field_validator


MIN_RESEARCH_SOURCES = 5
MAX_RESEARCH_SOURCES = 10
MIN_EXCERPT_CHARS = 120
MAX_EXCERPT_CHARS = 2600


class InsufficientResearchError(RuntimeError):
    """Raised when a deep article cannot assemble a truthful evidence pack."""


class ResearchSource(BaseModel):
    """One extension source that the final writer may actually cite."""

    title: str = Field(min_length=3, max_length=240)
    url: str = Field(min_length=10, max_length=2000)
    publisher: str = Field(min_length=2, max_length=160)
    excerpt: str = Field(min_length=MIN_EXCERPT_CHARS, max_length=MAX_EXCERPT_CHARS)
    evidence_role: Literal["official", "data", "analysis", "countercase"] = "analysis"

    @field_validator("title", "publisher", "excerpt", mode="before")
    @classmethod
    def _clean_text(cls, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    @field_validator("url", mode="before")
    @classmethod
    def _clean_url(cls, value: object) -> str:
        url = _canonical_url(str(value or ""))
        if not url.startswith(("https://", "http://")):
            raise ValueError("research source must use http(s)")
        return url


_TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
}


def _canonical_url(url: str) -> str:
    """Drop fragments and common campaign parameters without guessing redirects."""
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_KEYS
    ]
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), "")
    ).rstrip("/")


def validate_research_sources(
    sources: Iterable[ResearchSource | dict],
    *,
    minimum: int = MIN_RESEARCH_SOURCES,
    maximum: int = MAX_RESEARCH_SOURCES,
) -> list[ResearchSource]:
    """Deduplicate and enforce the five-to-ten-source deep-research contract."""
    accepted: list[ResearchSource] = []
    seen: set[str] = set()
    for item in sources:
        source = item if isinstance(item, ResearchSource) else ResearchSource.model_validate(item)
        key = _canonical_url(source.url)
        if key in seen:
            continue
        seen.add(key)
        accepted.append(source)
        if len(accepted) >= maximum:
            break
    if len(accepted) < minimum:
        raise InsufficientResearchError(
            f"deep research requires {minimum}-{maximum} usable extension sources; "
            f"only {len(accepted)} passed"
        )
    return accepted


_HIGH_SIGNAL_DOMAINS = (
    ".gov",
    ".edu",
    "sec.gov",
    "investor.",
    "ir.",
    "nber.org",
    "nature.com",
    "science.org",
    "pubmed",
    "arxiv.org",
    "oecd.org",
    "worldbank.org",
    "imf.org",
)

_LOW_SIGNAL_DOMAINS = (
    "wikipedia.org",
    "reddit.com",
    "quora.com",
    "pinterest.",
    "facebook.com",
    "x.com",
)


def _role_for(url: str, title: str) -> Literal["official", "data", "analysis", "countercase"]:
    haystack = f"{url} {title}".lower()
    if any(token in haystack for token in ("sec.gov", "investor.", "ir.", ".gov", "official")):
        return "official"
    if any(
        token in haystack
        for token in (
            "dataset",
            "statistics",
            "survey",
            "nber",
            "research",
            "study",
            "arxiv",
            "nature.com",
            "science.org",
            "working paper",
        )
    ):
        return "data"
    if any(token in haystack for token in ("critique", "risk", "against", "counter", "bear case")):
        return "countercase"
    return "analysis"


def _publisher_for(url: str) -> str:
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    return host or "unknown publisher"


def _search_candidates(queries: Sequence[str], *, per_query: int = 8) -> list[dict]:
    try:
        from ddgs import DDGS
    except Exception as exc:  # pragma: no cover - environment-specific dependency gate
        raise InsufficientResearchError(
            "ddgs is unavailable; install requirements.txt before deep composition"
        ) from exc

    candidates: list[dict] = []
    seen: set[str] = set()
    for query_index, query in enumerate(queries):
        query = (query or "").strip()
        if not query:
            continue
        try:
            results = DDGS().text(query, max_results=per_query)
        except Exception as exc:
            print(f"[EditorialResearch] search skipped for {query!r}: {exc}")
            continue
        for rank, result in enumerate(results or []):
            url = _canonical_url(result.get("href") or result.get("url") or "")
            if not url or url in seen or any(domain in url.lower() for domain in _LOW_SIGNAL_DOMAINS):
                continue
            seen.add(url)
            title = re.sub(r"\s+", " ", result.get("title") or "").strip()
            snippet = re.sub(r"\s+", " ", result.get("body") or "").strip()
            signal = 2 if any(domain in url.lower() for domain in _HIGH_SIGNAL_DOMAINS) else 0
            candidates.append(
                {
                    "title": title or _publisher_for(url),
                    "url": url,
                    "snippet": snippet,
                    "score": signal - query_index * 0.2 - rank * 0.02,
                }
            )
    return sorted(candidates, key=lambda item: item["score"], reverse=True)


def build_research_pack(
    queries: Sequence[str],
    *,
    primary_url: str | None = None,
    seed_sources: Sequence[dict] | None = None,
) -> list[ResearchSource]:
    """Search, read, and validate five to ten sources for one deep article.

    Seed URLs from a supplied bundle are read first, then query results fill the
    remaining slots.  A domain may contribute at most two sources so one outlet
    cannot masquerade as independent corroboration.
    """
    candidates: list[dict] = []
    for item in seed_sources or []:
        url = _canonical_url(str(item.get("url") or ""))
        if url:
            candidates.append(
                {
                    "title": item.get("title") or _publisher_for(url),
                    "url": url,
                    "snippet": item.get("excerpt") or item.get("body") or "",
                    "score": 3,
                }
            )
    candidates.extend(_search_candidates(queries))

    primary = _canonical_url(primary_url or "")
    seen: set[str] = set()
    candidate_domains: Counter[str] = Counter()
    eligible: list[dict] = []
    try:
        from scripts.submit_source import _fetch_page_text
    except Exception as exc:  # pragma: no cover - repository import should exist
        raise InsufficientResearchError("page-text fetcher is unavailable") from exc

    for item in candidates:
        url = _canonical_url(item.get("url") or "")
        if not url or url == primary or url in seen:
            continue
        domain = _publisher_for(url)
        if candidate_domains[domain] >= 3:
            continue
        seen.add(url)
        candidate_domains[domain] += 1
        eligible.append({**item, "url": url, "domain": domain})
        if len(eligible) >= 30:
            break

    def _read(item: dict) -> str:
        try:
            return (_fetch_page_text(item["url"]) or "").strip()
        except Exception:
            return ""

    with ThreadPoolExecutor(max_workers=6) as pool:
        page_texts = list(pool.map(_read, eligible))

    domains: Counter[str] = Counter()
    accepted: list[ResearchSource] = []
    for item, page_text in zip(eligible, page_texts):
        url = item["url"]
        domain = item["domain"]
        if domains[domain] >= 2:
            continue
        # A search-result snippet is discovery metadata, not evidence.  Count a
        # source only after the page-text reader actually obtained enough text.
        excerpt = re.sub(r"\s+", " ", page_text).strip()
        if len(excerpt) < MIN_EXCERPT_CHARS:
            continue
        try:
            source = ResearchSource(
                title=item.get("title") or domain,
                url=url,
                publisher=domain,
                excerpt=excerpt[:MAX_EXCERPT_CHARS],
                evidence_role=_role_for(url, item.get("title") or ""),
            )
        except Exception:
            continue
        accepted.append(source)
        domains[domain] += 1
        if len(accepted) >= MAX_RESEARCH_SOURCES:
            break

    return validate_research_sources(accepted)


def prompt_block(sources: Sequence[ResearchSource]) -> str:
    """Render a compact, attributable evidence pack for the final writer."""
    validated = validate_research_sources(sources)
    blocks = []
    for index, source in enumerate(validated, 1):
        blocks.append(
            f"[{index}] {source.title}\n"
            f"Publisher: {source.publisher}\n"
            f"Role: {source.evidence_role}\n"
            f"URL: {source.url}\n"
            f"Readable evidence: {source.excerpt}"
        )
    return "\n\n".join(blocks)
