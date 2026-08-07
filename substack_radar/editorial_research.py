"""Evidence-pack builder for deep Substack articles.

Podcast and company drafts use this module between the source-digest call and
the final writer call.  Search results do not become evidence merely because a
URL exists: each accepted source needs a readable excerpt, a unique URL, and a
clear publisher.  Fewer than five usable extension sources fails closed.
"""

from __future__ import annotations

import html
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, Literal, Sequence
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field, field_validator


MIN_RESEARCH_SOURCES = 5
MAX_RESEARCH_SOURCES = 10
MIN_EXCERPT_CHARS = 120
MAX_EXCERPT_CHARS = 2600
MAX_SOCIAL_EXCERPT_CHARS = 1600
MIN_SOCIAL_DIRECT_CHARS = 120


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


class SocialSignal(BaseModel):
    """Public social material that remains below the evidence tier."""

    platform: Literal["reddit", "x"]
    title: str = Field(min_length=3, max_length=240)
    url: str = Field(min_length=10, max_length=2000)
    excerpt: str = Field(default="", max_length=MAX_SOCIAL_EXCERPT_CHARS)
    access_method: Literal["public_search", "public_page", "public_oembed"]
    evidence_status: Literal["attributed_claim", "discovery_only"]

    @field_validator("title", "excerpt", mode="before")
    @classmethod
    def _clean_social_text(cls, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    @field_validator("url", mode="before")
    @classmethod
    def _clean_social_url(cls, value: object) -> str:
        url = _canonical_url(str(value or ""))
        if not url.startswith(("https://", "http://")):
            raise ValueError("social signal must use http(s)")
        return url


ReachHealth = Literal["available_public", "lead_only", "unavailable", "degraded"]


class SocialReachReport(BaseModel):
    """Signals plus health and durable-source follow-up queries."""

    signals: list[SocialSignal] = Field(default_factory=list)
    health: dict[Literal["reddit", "x"], ReachHealth]
    upstream_queries: list[str] = Field(default_factory=list, max_length=6)


class EditorialResearchBundle(BaseModel):
    """Validated evidence and non-evidence social context kept separate."""

    evidence_sources: list[ResearchSource]
    social_reach: SocialReachReport


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


def _social_platform(url: str) -> Literal["reddit", "x"] | None:
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    if host.endswith("reddit.com"):
        return "reddit"
    if host in {"x.com", "twitter.com", "mobile.twitter.com"}:
        return "x"
    return None


def _canonical_social_url(url: str, platform: Literal["reddit", "x"]) -> str:
    canonical = _canonical_url(url)
    parts = urlsplit(canonical)
    host = "reddit.com" if platform == "reddit" else "x.com"
    return urlunsplit(("https", host, parts.path, parts.query, "")).rstrip("/")


def _public_read_url(url: str, platform: Literal["reddit", "x"]) -> str:
    if platform == "reddit":
        parts = urlsplit(url)
        return urlunsplit(("https", "old.reddit.com", parts.path, parts.query, ""))
    return url


def _read_x_oembed(url: str) -> str:
    """Read public X post text through X's official no-cookie oEmbed endpoint."""
    endpoint = (
        "https://publish.twitter.com/oembed?omit_script=1&dnt=1&url="
        + quote(url, safe="")
    )
    request = Request(endpoint, headers={"User-Agent": "news-radar-social-reach/1.0"})
    with urlopen(request, timeout=12) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    raw_html = str(payload.get("html") or "")
    text = re.sub(r"<[^>]+>", " ", raw_html)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _read_reddit_json(url: str) -> str:
    """Read a public Reddit post through its credential-free JSON surface."""
    match = re.search(r"/comments/([a-z0-9]+)/", url, flags=re.IGNORECASE)
    if not match:
        return ""
    endpoint = f"https://www.reddit.com/comments/{match.group(1)}.json?raw_json=1"
    request = Request(endpoint, headers={"User-Agent": "news-radar-social-reach/1.0"})
    with urlopen(request, timeout=12) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    post = payload[0]["data"]["children"][0]["data"]
    author = str(post.get("author") or "unknown")
    title = str(post.get("title") or "")
    body = str(post.get("selftext") or "")
    return f"Reddit u/{author}: {title}\n\n{body}".strip()


def _default_social_reader(url: str) -> str:
    platform = _social_platform(url)
    if platform == "x":
        try:
            return _read_x_oembed(url)
        except Exception:
            return ""
    if platform == "reddit":
        try:
            direct = _read_reddit_json(url)
            if len(direct) >= MIN_SOCIAL_DIRECT_CHARS:
                return direct
        except Exception:
            pass
    try:
        from scripts.submit_source import _fetch_page_text

        return (_fetch_page_text(url) or "").strip()
    except Exception:
        return ""


def _default_social_search(query: str, max_results: int = 4) -> Iterable[dict]:
    try:
        from ddgs import DDGS
    except Exception:
        return []
    return DDGS().text(query, max_results=max_results) or []


def collect_social_reach(
    queries: Sequence[str],
    *,
    searcher: Callable[[str, int], Iterable[dict]] | None = None,
    reader: Callable[[str], str | None] | None = None,
    max_per_platform: int = 2,
) -> SocialReachReport:
    """Collect public X/Reddit signals without credentials or anti-bot bypass.

    Directly readable public text becomes an attributed claim, never independent
    corroboration. Search snippets remain discovery-only and may only generate a
    follow-up query for durable sources.
    """
    search = searcher or _default_social_search
    read = reader or _default_social_reader
    signals: list[SocialSignal] = []
    health: dict[Literal["reddit", "x"], ReachHealth] = {
        "reddit": "unavailable",
        "x": "unavailable",
    }
    seen: set[str] = set()

    for platform, site_query in (("reddit", "site:reddit.com"), ("x", "site:x.com")):
        platform_signals: list[SocialSignal] = []
        search_failed = False
        for base_query in [str(item).strip() for item in queries if str(item).strip()][:3]:
            if len(platform_signals) >= max_per_platform:
                break
            try:
                results = search(f"{site_query} {base_query}", 4)
            except Exception:
                search_failed = True
                continue
            for result in results or []:
                raw_url = str(result.get("href") or result.get("url") or "")
                detected = _social_platform(raw_url)
                if detected != platform:
                    continue
                path = urlsplit(raw_url).path.lower()
                if platform == "reddit" and "/comments/" not in path:
                    continue
                if platform == "x" and "/status/" not in path:
                    continue
                url = _canonical_social_url(raw_url, platform)
                if not url or url in seen:
                    continue
                seen.add(url)
                title = re.sub(r"\s+", " ", str(result.get("title") or "")).strip()
                snippet = re.sub(r"\s+", " ", str(result.get("body") or "")).strip()
                try:
                    direct = re.sub(
                        r"\s+",
                        " ",
                        str(read(_public_read_url(url, platform)) or ""),
                    ).strip()
                except Exception:
                    direct = ""
                readable = len(direct) >= MIN_SOCIAL_DIRECT_CHARS
                platform_signals.append(
                    SocialSignal(
                        platform=platform,
                        title=(title or url)[:240],
                        url=url,
                        excerpt=(direct if readable else snippet)[:MAX_SOCIAL_EXCERPT_CHARS],
                        access_method=(
                            "public_oembed"
                            if readable and platform == "x"
                            else "public_page"
                            if readable
                            else "public_search"
                        ),
                        evidence_status=(
                            "attributed_claim" if readable else "discovery_only"
                        ),
                    )
                )
                if len(platform_signals) >= max_per_platform:
                    break

        signals.extend(platform_signals)
        if any(item.evidence_status == "attributed_claim" for item in platform_signals):
            health[platform] = "available_public"
        elif platform_signals:
            health[platform] = "lead_only"
        elif search_failed:
            health[platform] = "degraded"

    upstream_queries: list[str] = []
    for signal in signals:
        query = re.sub(r"\s+", " ", signal.title).strip(" -|—")
        query = re.sub(
            r"^r/[^:]+\s+on\s+Reddit:\s*",
            "",
            query,
            flags=re.IGNORECASE,
        )
        if query.lower() in {"x.com", "twitter.com"} and signal.excerpt:
            query = re.split(
                r"(?<=[.!?。！？])\s+",
                signal.excerpt,
                maxsplit=1,
            )[0][:220].strip()
        if query.lower().startswith(("x.com/", "twitter.com/")):
            continue
        if query and query not in upstream_queries:
            upstream_queries.append(query)
        if len(upstream_queries) >= 4:
            break
    return SocialReachReport(
        signals=signals,
        health=health,
        upstream_queries=upstream_queries,
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


def build_research_bundle(
    queries: Sequence[str],
    *,
    primary_url: str | None = None,
    seed_sources: Sequence[dict] | None = None,
) -> EditorialResearchBundle:
    """Follow social leads, then build the independent durable evidence pack."""
    social_reach = collect_social_reach(queries)
    expanded_queries: list[str] = []
    for query in [*queries, *social_reach.upstream_queries]:
        clean = re.sub(r"\s+", " ", str(query or "")).strip()
        if clean and clean not in expanded_queries:
            expanded_queries.append(clean)
    evidence_sources = build_research_pack(
        expanded_queries,
        primary_url=primary_url,
        seed_sources=seed_sources,
    )
    return EditorialResearchBundle(
        evidence_sources=evidence_sources,
        social_reach=social_reach,
    )


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


def social_prompt_block(report: SocialReachReport | dict | None) -> str:
    """Render social context with an explicit non-evidence boundary."""
    reach = (
        report
        if isinstance(report, SocialReachReport)
        else SocialReachReport.model_validate(
            report
            or {
                "signals": [],
                "health": {"reddit": "unavailable", "x": "unavailable"},
                "upstream_queries": [],
            }
        )
    )
    lines = [
        "=== 社群觸達（低於證據層）===",
        f"平台健康：Reddit={reach.health['reddit']}；X={reach.health['x']}",
        "attributed_claim：已讀到公開原文，只能具名呈現為某人或社群的主張，"
        "並附原始連結；它不能證明自己，也不能取代上方 5–10 個延伸證據。",
        "discovery_only：只有搜尋線索，不得引用、改寫成事實或暗示已讀原文；"
        "只能用來理解為何要追查某個問題。",
        "按讚、轉貼、留言數不等於真實性或重要性，不得當成論證權重。",
    ]
    if not reach.signals:
        lines.append("本次沒有可用社群訊號；不要補寫或假裝看過任何貼文。")
    for index, signal in enumerate(reach.signals, 1):
        lines.extend(
            (
                f"[S{index}] {signal.platform.upper()} / {signal.evidence_status}",
                f"Title: {signal.title}",
                f"URL: {signal.url}",
                f"Access: {signal.access_method}",
                f"Content: {signal.excerpt or '（只有網址與標題）'}",
            )
        )
    return "\n".join(lines)
