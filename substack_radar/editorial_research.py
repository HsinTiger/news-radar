"""Evidence-pack builder for deep Substack articles.

Podcast and company drafts use this module between the source-digest call and
the final writer call.  Search results do not become evidence merely because a
URL exists: each accepted source needs a readable excerpt, a unique URL, and a
clear publisher.  Fewer than five usable extension sources fails closed.
"""

from __future__ import annotations

import html
import json
import ipaddress
import re
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, Literal, Sequence
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field, field_validator


MIN_RESEARCH_SOURCES = 5
MAX_RESEARCH_SOURCES = 10
MIN_EXCERPT_CHARS = 120
# 抓回全文之後才做的第二道相關性門檻。第一道只看搜尋摘要、命中 1 個詞就放行，
# 於是 2026-08-18 的瑞昱稿收到「YouTube 測試不可略過廣告」「Arteris 財報」這種
# 完全無關的來源。摘要是發現用的線索，全文才是證據。
MIN_PAGE_RELEVANCE_HITS = 3
# 補搜輪數：一輪淘完不夠 5 個就換問法再找，而不是把勉強及格的塞滿。
MAX_SEARCH_ROUNDS = 3
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


def is_fetch_safe(url: str) -> bool:
    """Whether this URL may be fetched and have its text put in model context.

    The pipeline reads each candidate page and feeds the text to the writer, so
    a URL is an inbound channel, not just a citation. Two classes are refused
    outright: schemes other than http(s) (``file:``, ``data:``, ``gopher:``),
    and hosts that resolve to the machine or its network — a search result
    pointing at ``localhost`` or ``169.254.169.254`` is never a real source and
    is exactly the shape an SSRF attempt takes.
    """
    parts = urlsplit((url or "").strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return False
    host = parts.hostname.lower()
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith((".local", ".internal")):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True  # a name, not a literal — DNS rebinding is out of scope here
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
    )


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

# 2026-08-09：原本只有「低訊號」網域（維基、論壇），沒有安全性把關。
# 實測一篇亞當·斯密文章抓回兩個 NSFW subreddit 聚合站、一個俄文影片下載站
# 與兩個工具首頁。這些不只印在來源清單，內容還會被讀進模型 context——
# 那是 prompt injection 的入口，不只是難看而已。
#
# 為什麼會抓到：SEO 寄生站專門針對熱門關鍵字（"reddit"、"download"）做排名，
# 搜尋引擎照樣回傳。這不需要有人刻意攻擊 AI，一般 SEO 垃圾就足以污染結果；
# 但同一條路徑確實也是刻意投毒可以走的路，所以按「不可信」處理。
_UNSAFE_DOMAINS = (
    "nsfw", "porn", "xxx", "adult", "onlyfans", "escort",
    "reddtastic", "snapwc", "bang.com", "bolt.new", "elicit.com",
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


_TERM_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "from", "that", "this", "what", "how",
        "why", "www", "com", "http", "https", "html", "index", "news",
        "報導", "分析", "影響", "如何", "為何", "什麼", "可能", "目前",
        "我們", "他們", "這個", "那個", "以及", "但是", "因為", "所以",
    }
)


def _subject_terms(*texts: object) -> set[str]:
    """Key terms describing what this article is actually about.

    Chinese has no whitespace to tokenise on, so CJK runs become overlapping
    bigrams — 「台積電」 yields 台積/積電, which still matches a candidate that
    spells the company out. Latin tokens keep their word boundaries.
    """
    terms: set[str] = set()
    for text in texts:
        value = str(text or "")
        terms |= {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9.\-]{2,}", value)}
        for run in re.findall(r"[一-鿿]{2,}", value):
            terms |= {run[i : i + 2] for i in range(len(run) - 1)}
    return {t for t in terms if t not in _TERM_STOPWORDS}


def _relevance_hits(text: str, subject_terms: set[str]) -> int:
    """How many of the article's own key terms this candidate actually mentions.

    Latin terms also match as substrings, because the subject is usually
    described in Chinese while the best sources are English: a piece about
    「Strategy 的比特幣持倉」 must not lose ``MicroStrategy bitcoin holdings``
    just because that token is not character-identical to ``strategy``. The
    4-character floor keeps short fragments from matching everything.
    """
    if not subject_terms:
        return 1
    found = _subject_terms(text)
    hits = found & subject_terms
    latin_subject = {t for t in subject_terms - hits if len(t) >= 4 and t.isascii()}
    latin_found = {t for t in found if len(t) >= 4 and t.isascii()}
    for term in latin_subject:
        if any(term in other or other in term for other in latin_found):
            hits = hits | {term}
    return len(hits)


def _is_legible(text: str) -> bool:
    """Reject titles that arrive as mojibake or replacement characters.

    Search results occasionally carry mis-decoded bytes; those reach the reader
    verbatim in the sources list, so they are cheaper to drop than to repair.
    """
    value = str(text or "")
    if not value:
        return True
    if "�" in value:
        return False
    junk = sum(1 for ch in value if unicodedata.category(ch) in {"Cc", "Co", "Cs", "Cn"})
    return junk / len(value) < 0.1


def _search_candidates(
    queries: Sequence[str],
    *,
    per_query: int = 8,
    subject_terms: set[str] | None = None,
) -> list[dict]:
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
            # 安全閘：擋在抓取之前，這些網址的內容不該進入模型 context。
            _haystack = f"{url} {result.get('title') or ''}".lower()
            if any(bad in _haystack for bad in _UNSAFE_DOMAINS) or not is_fetch_safe(url):
                print(f"[EditorialResearch] blocked unsafe source: {url[:70]}")
                continue
            # 裸首頁不是可查證的來源，讀者點過去看不到本文引用的資料。
            if _canonical_url(url).rstrip("/").count("/") <= 2:
                continue
            title = re.sub(r"\s+", " ", result.get("title") or "").strip()
            snippet = re.sub(r"\s+", " ", result.get("body") or "").strip()
            if not _is_legible(title) or not _is_legible(snippet):
                print(f"[EditorialResearch] dropped unreadable result: {url[:60]}")
                continue
            # 相關性閘：先前的分數只有域名權威度與搜尋排名，沒有任何一項在問
            # 「這篇跟本文有關嗎」，於是一個高權威網站的離題結果會贏過小網站的
            # 切題結果——就是 owner 看到「真的網址但一點都不相干」的來源。
            hits = _relevance_hits(f"{title} {snippet}", subject_terms or set())
            if not hits:
                continue
            seen.add(url)
            signal = 2 if any(domain in url.lower() for domain in _HIGH_SIGNAL_DOMAINS) else 0
            candidates.append(
                {
                    "title": title or _publisher_for(url),
                    "url": url,
                    "snippet": snippet,
                    # 相關性排在權威度前面：離題的路透不如切題的產業媒體。
                    "score": min(hits, 6) * 1.5 + signal - query_index * 0.2 - rank * 0.02,
                }
            )
    return sorted(candidates, key=lambda item: item["score"], reverse=True)


def _widen_queries(queries: Sequence[str], subject: str, round_index: int) -> list[str]:
    """補搜用的問法。同一組問句再搜一次只會拿到同一批結果，所以要換角度。

    第 2 輪：把主題詞跟原問句的關鍵詞重新組合（更聚焦在主題本身）。
    第 3 輪：只用主題詞加上通用的證據型詞彙（財報／數據／市佔／分析）。
    回空陣列代表沒有可用的新問法，呼叫端就停止補搜——寧可來源少而準，
    也不要為了湊數把離題結果放進讀者看得到的清單。
    """
    subject_text = re.sub(r"\s+", " ", str(subject or "")).strip()
    core = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9.\-]{2,}|[一-鿿]{2,}", subject_text)][:4]
    if not core:
        return []
    if round_index == 1:
        heads = [re.sub(r"\s+", " ", q).strip() for q in queries if (q or "").strip()][:3]
        out = [f"{' '.join(core[:2])} {h}"[:120] for h in heads]
    else:
        out = [f"{' '.join(core[:3])} {tail}" for tail in ("財報 數據", "市佔 分析", "風險 爭議")]
    return [q for q in dict.fromkeys(out) if q]


def build_research_pack(
    queries: Sequence[str],
    *,
    primary_url: str | None = None,
    seed_sources: Sequence[dict] | None = None,
    subject: str = "",
) -> list[ResearchSource]:
    """Search, read, and validate five to ten sources for one deep article.

    Seed URLs from a supplied bundle are read first, then query results fill the
    remaining slots.  A domain may contribute at most two sources so one outlet
    cannot masquerade as independent corroboration.
    """
    # 主題詞彙來自主來源標題加上研究問句本身——查證問句就是這篇在問的事。
    subject_terms = _subject_terms(subject, *queries)

    try:
        from scripts.submit_source import _fetch_page_text
    except Exception as exc:  # pragma: no cover - repository import should exist
        raise InsufficientResearchError("page-text fetcher is unavailable") from exc

    def _read(item: dict) -> str:
        try:
            return (_fetch_page_text(item["url"]) or "").strip()
        except Exception:
            return ""

    seed_candidates: list[dict] = []
    for item in seed_sources or []:
        url = _canonical_url(str(item.get("url") or ""))
        if url:
            seed_candidates.append(
                {
                    "title": item.get("title") or _publisher_for(url),
                    "url": url,
                    "snippet": item.get("excerpt") or item.get("body") or "",
                    "score": 3,
                    "seed": True,
                }
            )

    primary = _canonical_url(primary_url or "")
    seen: set[str] = set()
    domains: Counter[str] = Counter()
    accepted: list[ResearchSource] = []
    rejected_offtopic = 0
    rejected_thin = 0

    # Loop：一輪＝搜尋 → 抓全文 → 用全文驗相關性 → 收下合格的。
    # 收不滿最低門檻就換問法再跑一輪，而不是把勉強及格的湊數塞進去。
    # 這是 2026-08-18 稽核的直接結果：舊版只用搜尋摘要驗一次、命中 1 個詞就過。
    for round_index in range(MAX_SEARCH_ROUNDS):
        if len(accepted) >= MIN_RESEARCH_SOURCES:
            break
        if round_index == 0:
            batch = list(seed_candidates)
            batch.extend(_search_candidates(queries, subject_terms=subject_terms))
        else:
            widened = _widen_queries(queries, subject, round_index)
            if not widened:
                break
            print(f"[EditorialResearch] 只收到 {len(accepted)} 個合格來源，"
                  f"換問法補搜第 {round_index + 1}/{MAX_SEARCH_ROUNDS} 輪")
            batch = _search_candidates(widened, subject_terms=subject_terms)

        eligible: list[dict] = []
        candidate_domains: Counter[str] = Counter()
        for item in batch:
            url = _canonical_url(item.get("url") or "")
            if not url or url == primary or url in seen:
                continue
            domain = _publisher_for(url)
            if candidate_domains[domain] >= 3 or domains[domain] >= 2:
                continue
            seen.add(url)
            candidate_domains[domain] += 1
            eligible.append({**item, "url": url, "domain": domain})
            if len(eligible) >= 30:
                break
        if not eligible:
            continue

        with ThreadPoolExecutor(max_workers=6) as pool:
            page_texts = list(pool.map(_read, eligible))

        for item, page_text in zip(eligible, page_texts):
            if domains[item["domain"]] >= 2:
                continue
            # 搜尋摘要是發現用的線索，不是證據。只有抓得回來的全文才算數。
            excerpt = re.sub(r"\s+", " ", page_text).strip()
            if len(excerpt) < MIN_EXCERPT_CHARS:
                rejected_thin += 1
                continue
            # 第二道相關性：對全文，而且門檻比搜尋摘要那道高。
            # seed 來源是上游明確指定的素材，不受此門檻限制。
            if not item.get("seed"):
                hits = _relevance_hits(f"{item.get('title', '')} {excerpt}", subject_terms)
                if hits < MIN_PAGE_RELEVANCE_HITS:
                    rejected_offtopic += 1
                    print(f"[EditorialResearch] 淘汰離題來源（全文命中 {hits}"
                          f" < {MIN_PAGE_RELEVANCE_HITS}）：{item['url'][:66]}")
                    continue
            try:
                source = ResearchSource(
                    title=item.get("title") or item["domain"],
                    url=item["url"],
                    publisher=item["domain"],
                    excerpt=excerpt[:MAX_EXCERPT_CHARS],
                    evidence_role=_role_for(item["url"], item.get("title") or ""),
                )
            except Exception:
                continue
            accepted.append(source)
            domains[item["domain"]] += 1
            if len(accepted) >= MAX_RESEARCH_SOURCES:
                break

    if rejected_offtopic or rejected_thin:
        print(f"[EditorialResearch] 來源閘門：離題淘汰 {rejected_offtopic}、"
              f"內容太少淘汰 {rejected_thin}、收下 {len(accepted)}")
    return validate_research_sources(accepted)


def build_research_bundle(
    queries: Sequence[str],
    *,
    primary_url: str | None = None,
    seed_sources: Sequence[dict] | None = None,
    subject: str = "",
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
        subject=subject,
    )
    return EditorialResearchBundle(
        evidence_sources=evidence_sources,
        social_reach=social_reach,
    )


def _age_note(url: str, excerpt: str) -> str:
    """給模型看的來源新鮮度標註。過期的來源不一定要丟掉——舊報導講的機制
    往往仍然成立——但**它裡面的價格、目標價、市值、評等一定不能當現況寫**。
    2026-08-18 的聯發科稿就是把 9 個月前的目標價寫成當前的外資示警。"""
    from substack_radar.source_dates import age_days

    days = age_days(url, excerpt)
    if days is None:
        return "（日期不明）"
    if days <= 45:
        return f"（{days} 天前）"
    months = days // 30
    return (f"（**{months} 個月前**；裡面的股價／目標價／市值／評等已過期，"
            "只能當歷史敘述，不可寫成現況）")


def prompt_block(sources: Sequence[ResearchSource]) -> str:
    """Render a compact, attributable evidence pack for the final writer."""
    validated = validate_research_sources(sources)
    blocks = []
    for index, source in enumerate(validated, 1):
        blocks.append(
            f"[{index}] {source.title} {_age_note(source.url, source.excerpt)}\n"
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
