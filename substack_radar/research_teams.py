"""調研小組（2026-11-21 週末旗艦第 2 步）：voices／counter／timeline 三角色分工調研。

每個角色都是「程式找網頁與抓全文 → 便宜模型交回證據卡 → 程式驗證」。
寫手只看驗證過的卡與不足警告，不看未經核對的網頁摘要或模型推測。
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
CACHE_DIR = REPO / "data" / "team_cache"

ROLES = ("voices", "counter", "timeline")
ROLE_TITLES = {
    "voices": "聲音組（公開發言）",
    "counter": "反方組（風險／批評／訴訟／監管／放空）",
    "timeline": "時間線組（關鍵轉折點）",
}

DEFAULT_MAX_QUERIES = 4
DEFAULT_MAX_PAGES = 6
DEFAULT_MAX_CHATS = 6
MAX_PAGES_PER_DOMAIN = 2
MAX_CARDS_PER_DOMAIN = 3
MAX_CARDS_PER_SPEAKER_ROLE = 3
MAX_CARDS_PER_SPEAKER = 2
MAX_PAGE_CHARS = 12000
CARDS_PER_PAGE = 5
MAX_CARDS_FOR_WRITER = 8

QUERY_TEMPLATES: dict[str, tuple[str, ...]] = {
    "voices": (
        "{s} CEO interview",
        "{s} analyst downgrade price target",
        "{s} critics say",
        "{s} rival competitor says",
    ),
    "counter": (
        "{s} criticism",
        "{s} lawsuit regulators",
        "{s} short seller report",
        "{s} risk factors competition",
    ),
    "timeline": (
        "{s} history founded",
        "{s} acquisition strategy shift",
        "{s} turning point",
        "{s} pivot",
    ),
}

_SPEAKER_ROLE_ALIASES = {
    "管理層": "管理層",
    "高層": "管理層",
    "執行長": "管理層",
    "management": "管理層",
    "executive": "管理層",
    "ceo": "管理層",
    "cfo": "管理層",
    "批評者": "批評者",
    "反對者": "批評者",
    "critic": "批評者",
    "critics": "批評者",
    "客戶": "客戶",
    "顧客": "客戶",
    "用戶": "客戶",
    "customer": "客戶",
    "customers": "客戶",
    "client": "客戶",
    "對手": "對手",
    "競爭對手": "對手",
    "競爭者": "對手",
    "competitor": "對手",
    "rival": "對手",
    "分析師": "分析師",
    "研究員": "分析師",
    "analyst": "分析師",
    "員工": "員工",
    "前員工": "員工",
    "employee": "員工",
    "staff": "員工",
    "worker": "員工",
}

_SEVERITY_ALIASES = {
    "常見但輕": "常見但輕",
    "常見": "常見但輕",
    "輕微": "常見但輕",
    "common_mild": "常見但輕",
    "common": "常見但輕",
    "mild": "常見但輕",
    "low": "常見但輕",
    "少見但致命": "少見但致命",
    "致命": "少見但致命",
    "嚴重": "少見但致命",
    "rare_fatal": "少見但致命",
    "fatal": "少見但致命",
    "severe": "少見但致命",
    "high": "少見但致命",
}

_NAME_SUFFIXES = frozenset({
    "jr", "sr", "ii", "iii", "iv", "phd", "md", "ceo", "cfo", "cto", "coo", "president",
    "vp", "dr", "mr", "ms", "mrs", "prof", "founder", "chair", "chairman",
})
_SPEAKER_TITLES = _NAME_SUFFIXES | frozenset({
    "chief", "executive", "officer", "chairwoman", "chairperson",
    "cofounder", "co-founder", "director", "miss", "sir",
})

_COMMON_RULES = """規則：
- 「引句」必須從原文一字不差地複製（英文原文，1～3 句，最多 500 字元），不可改寫或拼湊不連續段落。
- 「主張」用一句繁體中文說明這段引句證明了什麼，不能超出引句內容；主張裡的每一個阿拉伯數字都必須在引句裡找得到。
- 找不到明確證據就回空陣列 {{"cards": []}}，不要硬湊或捏造。"""

PROMPTS: dict[str, str] = {
    "voices": (
        "你是調研記者助理（聲音組），只讀下面這頁關於「{subject}」的原文，交回最多 {n} 張「公開發言證據卡」。\n"
        "優先挑記者轉述的分析師、競爭對手、前員工、監管機關、批評者或客戶的具體發言與評價，比執行長自述優先。\n\n"
        + _COMMON_RULES
        + "\n- 「說話者」填發言者姓名或機構名（照原文英文拼寫，且必須出現在引句前後附近）。\n"
        "- 「說話者角色」只能是：管理層／批評者／客戶／對手／分析師／員工。\n"
        '- 只輸出 JSON：{{"cards": [{{"主張": "", "引句": "", "說話者": "", "說話者角色": ""}}]}}\n\n'
        "=== 網頁原文（{url}）===\n{chunk}\n"
    ),
    "counter": (
        "你是調研記者助理（反方組），只讀下面這頁關於「{subject}」的原文，交回最多 {n} 張「風險與反方證據卡」。\n"
        "優先挑實質批評、訴訟、監管調查、放空質疑、結構性風險或競爭壓力，不要挑空泛套話。\n\n"
        + _COMMON_RULES
        + "\n- 「嚴重度」只能是：常見但輕／少見但致命。\n"
        '- 只輸出 JSON：{{"cards": [{{"主張": "", "引句": "", "嚴重度": ""}}]}}\n\n'
        "=== 網頁原文（{url}）===\n{chunk}\n"
    ),
    "timeline": (
        "你是調研記者助理（時間線組），只讀下面這頁關於「{subject}」的原文，交回最多 {n} 張「關鍵轉折點證據卡」。\n"
        "優先挑創立、關鍵併購、商業模式或策略大轉向、決定公司命運的里程碑事件。\n\n"
        + _COMMON_RULES
        + "\n- 「日期」格式只能是 YYYY 或 YYYY-MM（例如 2016 或 2022-11），且年份必須出現在引句裡或引句前後附近。\n"
        "- 「事件」用簡短繁體中文概括該轉折事件。\n"
        '- 只輸出 JSON：{{"cards": [{{"主張": "", "引句": "", "日期": "", "事件": ""}}]}}\n\n'
        "=== 網頁原文（{url}）===\n{chunk}\n"
    ),
}


@dataclass
class TeamCard:
    role: str
    claim: str
    quote: str
    url: str
    domain: str
    tier: str
    speaker: str = ""
    speaker_role: str = ""
    date: str = ""
    event: str = ""
    severity: str = ""


@dataclass
class TeamReport:
    role: str
    cards: list[TeamCard] = field(default_factory=list)
    met: bool = False
    stats: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    titles: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 查詢與預算
# ---------------------------------------------------------------------------

def role_queries(
    role: str,
    subject: str,
    taiwan_query: str = "",
    *,
    max_queries: int = DEFAULT_MAX_QUERIES,
) -> list[str]:
    """產生指定角色的搜尋查詢（最多 4 條；可選附上一條中文台灣查詢）。"""
    if role not in QUERY_TEMPLATES:
        raise ValueError(f"未知角色：{role}")
    s = re.sub(r"\s+", " ", str(subject or "")).strip()
    if not s or max_queries <= 0:
        return []
    base = [tpl.format(s=s) for tpl in QUERY_TEMPLATES[role]]
    tw = re.sub(r"\s+", " ", str(taiwan_query or "")).strip()
    if tw:
        if max_queries >= len(base) + 1:
            return (base + [tw])[:max_queries]
        keep = max(0, max_queries - 1)
        return (base[:keep] + [tw])[:max_queries]
    return base[:max_queries]


def _parse_budget(budget: Any) -> tuple[int, int, int]:
    if budget is None:
        return DEFAULT_MAX_QUERIES, DEFAULT_MAX_PAGES, DEFAULT_MAX_CHATS
    if isinstance(budget, dict):
        q = budget.get("queries", budget.get("max_queries", DEFAULT_MAX_QUERIES))
        p = budget.get("pages", budget.get("max_pages", budget.get("fetch", DEFAULT_MAX_PAGES)))
        c = budget.get("chats", budget.get("max_chats", budget.get("calls", budget.get("model_calls", DEFAULT_MAX_CHATS))))
        return max(0, int(q)), max(0, int(p)), max(0, int(c))
    q = getattr(budget, "queries", getattr(budget, "max_queries", DEFAULT_MAX_QUERIES))
    p = getattr(budget, "pages", getattr(budget, "max_pages", getattr(budget, "fetch", DEFAULT_MAX_PAGES)))
    c = getattr(budget, "chats", getattr(budget, "max_chats", getattr(budget, "calls", DEFAULT_MAX_CHATS)))
    return max(0, int(q)), max(0, int(p)), max(0, int(c))


def _domain_of(url: str) -> str:
    return urlsplit((url or "").strip()).netloc.lower().removeprefix("www.") or "unknown"


# ---------------------------------------------------------------------------
# 程式驗證（引句、說話者距離、年份距離、數字比對）
# ---------------------------------------------------------------------------

_PUNCT_MAP = {
    "“": '"', "”": '"', "‘": "'", "’": "'", "「": '"', "」": '"', "『": '"', "』": '"',
    "，": ",", "。": ".", "：": ":", "；": ";", "！": "!", "？": "?", "（": "(", "）": ")",
}
_SKIP_NORM = frozenset("*_`#>")


def _norm_with_map(text: str) -> tuple[str, list[int]]:
    """產生跟 fact_audit._norm 一致的正規化字串，同時保留每個字元在原文的索引位置。"""
    chars: list[str] = []
    pos: list[int] = []
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("&gt;", i):
            chars.append(">")
            pos.append(i)
            i += 4
            continue
        if text.startswith("&lt;", i):
            chars.append("<")
            pos.append(i)
            i += 4
            continue
        if text.startswith("&amp;", i):
            chars.append("&")
            pos.append(i)
            i += 5
            continue
        ch = _PUNCT_MAP.get(text[i], text[i])
        if not ch.isspace() and ch not in _SKIP_NORM:
            chars.append(ch.lower())
            pos.append(i)
        i += 1
    return "".join(chars), pos


def _find_quote_spans(quote: str, chunk: str) -> list[tuple[int, int]]:
    """找出 quote 在 chunk 中出現的 (start, end) 區間，容許空白或少數標點差異。"""
    from substack_radar.fact_audit import _norm

    q = quote.strip()
    if not q or not chunk:
        return []
    exact = [m.span() for m in re.finditer(re.escape(q), chunk, re.IGNORECASE)]
    if exact:
        return exact

    nq = _norm(q)
    if not nq:
        return []
    nc, pos = _norm_with_map(chunk)
    if not nc:
        return []

    spans: list[tuple[int, int]] = []
    start_at = 0
    while True:
        idx = nc.find(nq, start_at)
        if idx == -1:
            break
        s = pos[idx]
        e = pos[min(len(pos) - 1, idx + len(nq) - 1)] + 1
        spans.append((s, e))
        start_at = idx + max(1, len(nq) // 2)
    if spans:
        return spans

    # coverage >= 0.9 但中間漏掉一兩個字：用頭尾命中的視窗定位區間
    cjk = len(re.findall(r"[一-鿿]", nq)) > len(nq) * 0.3
    size = 8 if cjk else 18
    if len(nq) >= size:
        hits: list[int] = []
        for i in range(0, len(nq) - size + 1, max(1, size // 2)):
            idx = nc.find(nq[i : i + size])
            if idx != -1:
                hits.append(idx)
        if hits:
            lo = min(hits)
            hi = max(h for h in hits if h - lo <= len(nq) * 1.5)
            s = pos[lo]
            e = pos[min(len(pos) - 1, hi + size - 1)] + 1
            return [(s, e)]
    return []


def _speaker_near_quote(speaker: str, chunk: str, spans: list[tuple[int, int]]) -> bool:
    """voices 規則：說話者的姓氏或全名要出現在頁面上引句前後 400 字內。"""
    raw = (speaker or "").strip()
    if not raw or not spans:
        return False

    candidates: list[str] = []
    # 含括號時（如「黃仁勳 (Jensen Huang)」），括號內外都拆出來比對
    parts = [re.sub(r"[\(（][^\)）]*[\)）]", " ", raw), *re.findall(r"[\(（]([^\)）]*)[\)）]", raw)]
    for part in parts:
        cleaned = part.split(",")[0].split("，")[0].strip()
        if not cleaned:
            continue
        candidates.append(cleaned)
        latin = [
            w for w in re.findall(r"[A-Za-z\u00C0-\u024F][A-Za-z\u00C0-\u024F'\-\.]*", cleaned)
            if w.rstrip(".").lower() not in _NAME_SUFFIXES
        ]
        if len(latin) >= 2:
            candidates.append(latin[-1])
            candidates.append(" ".join(latin))
        elif len(latin) == 1:
            candidates.append(latin[0])
        for cjk in re.findall(r"[一-鿿]{2,}", cleaned):
            candidates.append(cjk)

    seen_cand: list[str] = []
    for c in candidates:
        c = c.strip()
        if c and c not in seen_cand:
            seen_cand.append(c)

    for start, end in spans:
        window = chunk[max(0, start - 400) : min(len(chunk), end + 400)]
        win_lower = window.lower()
        for cand in seen_cand:
            if re.search(r"[一-鿿]", cand):
                if cand in window:
                    return True
            else:
                if re.search(rf"(?<![a-z0-9]){re.escape(cand.lower())}(?![a-z0-9])", win_lower):
                    return True
    return False


def _normalize_date(raw_date: str) -> str:
    m = re.search(r"(?<!\d)((?:19|20)\d{2}(?:-(?:0[1-9]|1[0-2]))?)(?!\d)", str(raw_date or ""))
    return m.group(1) if m else ""


def _year_of(date_str: str) -> str:
    m = re.search(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", str(date_str or ""))
    return m.group(1) if m else ""


def _year_near_quote(year: str, quote: str, chunk: str, spans: list[tuple[int, int]]) -> bool:
    """timeline 規則：date 的年份要出現在引句裡，或引句前後 200 字內。"""
    if not year:
        return False
    pat = re.compile(rf"(?<!\d){re.escape(year)}(?!\d)")
    if pat.search(quote):
        return True
    for start, end in spans:
        window = chunk[max(0, start - 200) : min(len(chunk), end + 200)]
        if pat.search(window):
            return True
    return False


def _numbers_in_quote(
    texts: Sequence[str],
    quote: str,
    *,
    exempt_years: frozenset[str] | set[str] = frozenset(),
) -> bool:
    """任何角色：卡上寫的數字都要出現在引句裡（支援 億／兆／萬 與千分位逗號）。"""
    flat_q = re.sub(r"(?<=\d)[,，](?=\d{3})", "", quote or "")
    have: set[float] = set()
    for m in re.finditer(r"\d+(?:\.\d+)?", flat_q):
        try:
            have.add(round(float(m.group(0)), 4))
        except ValueError:
            pass

    for text in texts:
        flat_t = re.sub(r"(?<=\d)[,，](?=\d{3})", "", str(text or ""))
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(億|兆|萬)?", flat_t):
            num_str, unit = m.group(1), m.group(2) or ""
            if num_str in exempt_years and not unit:
                continue
            try:
                v = float(num_str)
            except ValueError:
                continue
            if unit == "億":
                alts = (v, v / 10, v * 100, v * 100_000_000)
            elif unit == "兆":
                alts = (v, v * 1000, v * 1_000_000)
            elif unit == "萬":
                alts = (v, v * 10, v * 10_000, v / 100)
            else:
                alts = (v,)
            if any(round(alt, 4) in have for alt in alts):
                continue
            if any(h >= 1000 and abs(h - alt) <= h * 0.0005 for alt in alts for h in have):
                continue
            return False
    return True


def _pick_field(raw: dict, *keys: str) -> str:
    for k in keys:
        val = raw.get(k)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def _normalize_speaker_role(raw_role: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(raw_role or "")).strip()
    return _SPEAKER_ROLE_ALIASES.get(cleaned.lower(), _SPEAKER_ROLE_ALIASES.get(cleaned, cleaned))


def _normalize_speaker_name(speaker: str) -> str:
    """正規化說話者名稱（小寫、去括號附註與常見頭銜）以比對同一人。"""
    raw = re.sub(r"\s+", " ", str(speaker or "")).strip().lower()
    if not raw:
        return ""
    no_paren = re.sub(r"[\(（][^\)）]*[\)）]", " ", raw)
    base = no_paren.strip() or raw
    tokens = [
        tok for tok in re.findall(r"[^\s,，.。;；:：!！?？]+", base)
        if tok not in _SPEAKER_TITLES
    ]
    return " ".join(tokens) if tokens else base


def _normalize_severity(raw_sev: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(raw_sev or "")).strip()
    return _SEVERITY_ALIASES.get(cleaned.lower(), _SEVERITY_ALIASES.get(cleaned, cleaned))


def verify_card(
    role: str,
    raw: dict,
    chunk: str,
    haystack_norm: str,
    url: str,
    domain: str,
    page_tier: str,
) -> tuple[TeamCard | None, str]:
    """程式驗證單張證據卡。通過回 (TeamCard, '')，不通過回 (None, 原因)。"""
    from substack_radar.fact_audit import _norm, coverage

    claim = _pick_field(raw, "主張", "claim")
    quote = _pick_field(raw, "引句", "quote")
    extra_num = _pick_field(raw, "數字", "number")

    if not claim or not quote:
        return None, "空白"
    if len(_norm(quote)) < 10 or coverage(quote, haystack_norm) < 0.9:
        return None, "引句不在原文"

    spans = _find_quote_spans(quote, chunk)
    speaker = ""
    speaker_role = ""
    norm_date = ""
    event = ""
    severity = ""
    exempt_years: set[str] = set()
    num_texts = [claim, extra_num]

    if role == "voices":
        speaker = _pick_field(raw, "說話者", "speaker")
        speaker_role = _normalize_speaker_role(_pick_field(raw, "說話者角色", "角色", "speaker_role"))
        if not speaker:
            return None, "缺少說話者"
        if not speaker_role:
            return None, "缺少說話者角色"
        if not _speaker_near_quote(speaker, chunk, spans):
            return None, "說話者不在引句附近"
    elif role == "timeline":
        raw_date = _pick_field(raw, "日期", "date")
        norm_date = _normalize_date(raw_date)
        year = _year_of(norm_date)
        if not year:
            return None, "日期格式不符"
        if not _year_near_quote(year, quote, chunk, spans):
            return None, "年份不在引句附近"
        event = _pick_field(raw, "事件", "event") or claim
        exempt_years.add(year)
        num_texts.append(event)
    elif role == "counter":
        severity = _normalize_severity(_pick_field(raw, "嚴重度", "嚴重程度", "severity"))

    if not _numbers_in_quote(num_texts, quote, exempt_years=exempt_years):
        return None, "數字不在引句"

    return (
        TeamCard(
            role=role,
            claim=claim,
            quote=quote[:500],
            url=url,
            domain=domain,
            tier=page_tier,
            speaker=speaker,
            speaker_role=speaker_role,
            date=norm_date,
            event=event,
            severity=severity,
        ),
        "",
    )


# ---------------------------------------------------------------------------
# 角色執行與驗收
# ---------------------------------------------------------------------------

def _is_met(role: str, cards: list[TeamCard]) -> bool:
    if role == "voices":
        return len({c.speaker_role for c in cards if c.speaker_role}) >= 2
    if role == "counter":
        return len(cards) >= 3 and len({c.domain for c in cards if c.domain}) >= 2
    if role == "timeline":
        return len({_year_of(c.date) for c in cards if _year_of(c.date)}) >= 3
    return False


def _default_chat(prompt: str) -> tuple[str, str]:
    """共用文件組的讀卡函式：agy Gemini 3.8 Flash 優先，閘道 fast 後備（2026-11-21 Gemini 系優先）。"""
    from substack_radar.filings_desk import _default_chat as shared
    return shared(prompt)


def _default_fetch(url: str) -> str | None:
    from scripts.submit_source import _fetch_page_text

    return _fetch_page_text(url)


def _call_search(search: Callable | None, queries: list[str], subject: str) -> list[dict]:
    from substack_radar.editorial_research import _search_candidates, _subject_terms

    fn = search or _search_candidates
    terms = _subject_terms(subject)
    try:
        sig = inspect.signature(fn)
        params = sig.parameters
        has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        kw: dict[str, Any] = {}
        if has_varkw or "per_query" in params:
            kw["per_query"] = 8
        if has_varkw or "subject_terms" in params:
            kw["subject_terms"] = terms
        return list(fn(queries, **kw) or [])
    except (TypeError, ValueError):
        return list(fn(queries) or [])


def _parse_cards(raw_out: str) -> list[dict]:
    from substack_radar.fact_audit import loads_lenient, salvage

    data = loads_lenient(raw_out)
    items = None
    if isinstance(data, dict):
        items = data.get("cards") if isinstance(data.get("cards"), list) else data.get("items")
    if not isinstance(items, list):
        items = salvage(raw_out, "引句") or salvage(raw_out, "quote")
    return [it for it in (items or []) if isinstance(it, dict)]


def _order_candidates(role: str, candidates: list[dict]) -> list[dict]:
    """voices 且候選含 query_index 時依 query 分組輪流取；其餘照分數由高到低排。"""
    by_score = sorted(candidates, key=lambda c: float(c.get("score") or 0.0), reverse=True)
    if role != "voices" or not any(c.get("query_index") is not None for c in by_score):
        return by_score

    groups: dict[tuple[int, int | str], list[dict]] = {}
    for c in by_score:
        qi = c.get("query_index")
        if qi is None:
            key: tuple[int, int | str] = (1, 0)
        elif isinstance(qi, int) and not isinstance(qi, bool):
            key = (0, qi)
        else:
            try:
                key = (0, int(qi))
            except (TypeError, ValueError):
                key = (1, str(qi))
        groups.setdefault(key, []).append(c)

    ordered_groups = [groups[k] for k in sorted(groups)]
    out: list[dict] = []
    i = 0
    while any(i < len(g) for g in ordered_groups):
        for g in ordered_groups:
            if i < len(g):
                out.append(g[i])
        i += 1
    return out


def run_role(
    role: str,
    subject: str,
    *,
    search: Callable | None = None,
    fetch: Callable[[str], str | None] | None = None,
    chat: Callable[[str], Any] | None = None,
    budget: Any = None,
    taiwan_query: str = "",
) -> TeamReport:
    """執行單一調研角色：搜尋 → 挑頁讀全文 → 模型交卡 → 程式驗證 → 提早停止。"""
    if role not in ROLES:
        raise ValueError(f"未知角色：{role}")

    from substack_radar.editorial_research import _canonical_url, is_fetch_safe
    from substack_radar.fact_audit import _norm
    from substack_radar.source_tier import tier

    max_queries, max_pages, max_chats = _parse_budget(budget)
    queries = role_queries(role, subject, taiwan_query, max_queries=max_queries)

    rejected: Counter[str] = Counter()
    stats: dict[str, Any] = {
        "queries": 0,
        "pages": 0,
        "chats": 0,
        "rejected": {},
        "search_failed": False,
    }
    if not queries:
        return TeamReport(role=role, cards=[], met=False, stats=stats)

    stats["queries"] = len(queries)
    try:
        candidates = _call_search(search, queries, subject)
    except Exception as exc:  # noqa: BLE001
        print(f"[Teams] ⚠️ {role} 搜尋失敗：{str(exc)[:100]}")
        stats["search_failed"] = True
        return TeamReport(role=role, cards=[], met=False, stats=stats)

    sorted_cands = _order_candidates(role, candidates)

    fetch_fn = fetch or _default_fetch
    chat_fn = chat or _default_chat

    cards: list[TeamCard] = []
    titles: dict[str, str] = {}
    seen_urls: set[str] = set()
    seen_quotes: set[str] = set()
    pages_per_domain: Counter[str] = Counter()
    cards_per_domain: Counter[str] = Counter()
    cards_per_speaker_role: Counter[str] = Counter()
    cards_per_speaker: Counter[str] = Counter()
    pages_read = 0
    model_calls = 0
    model_name = ""
    met = False

    for item in sorted_cands:
        if met or pages_read >= max_pages or model_calls >= max_chats:
            break
        raw_url = str(item.get("url") or "").strip()
        if not raw_url or not is_fetch_safe(raw_url):
            continue
        canon = _canonical_url(raw_url) or raw_url
        if canon in seen_urls:
            continue
        page_tier = tier(raw_url)
        if page_tier == "D":
            continue
        domain = _domain_of(raw_url)
        if pages_per_domain[domain] >= MAX_PAGES_PER_DOMAIN:
            continue
        if cards_per_domain[domain] >= MAX_CARDS_PER_DOMAIN:
            continue

        seen_urls.add(canon)
        pages_per_domain[domain] += 1
        pages_read += 1

        try:
            full_text = (fetch_fn(raw_url) or "").strip()
        except Exception as exc:  # noqa: BLE001
            print(f"[Teams] ⚠️ {role} 抓頁失敗 {raw_url[:80]}：{str(exc)[:80]}")
            rejected["抓取失敗"] += 1
            continue
        if not full_text:
            rejected["網頁空白"] += 1
            continue

        chunk = full_text[:MAX_PAGE_CHARS]
        prompt = PROMPTS[role].format(
            subject=subject,
            n=CARDS_PER_PAGE,
            url=raw_url,
            chunk=chunk,
        )

        model_calls += 1
        try:
            resp = chat_fn(prompt)
            raw_out, served = resp if isinstance(resp, tuple) else (resp, "")
            if served and not model_name:
                model_name = str(served)
        except Exception as exc:  # noqa: BLE001
            print(f"[Teams] ⚠️ {role} 模型呼叫失敗：{str(exc)[:80]}")
            rejected["模型失敗"] += 1
            continue

        titles[raw_url] = str(item.get("title") or domain).strip()
        haystack_norm = _norm(chunk)

        for raw_card in _parse_cards(str(raw_out or "")):
            if cards_per_domain[domain] >= MAX_CARDS_PER_DOMAIN:
                rejected["同網域超過上限"] += 1
                continue
            card, why = verify_card(role, raw_card, chunk, haystack_norm, raw_url, domain, page_tier)
            if not card:
                rejected[why] += 1
                continue
            q_key = _norm(card.quote)
            if q_key in seen_quotes:
                rejected["重複引句"] += 1
                continue
            if role == "voices":
                if cards_per_speaker_role[card.speaker_role] >= MAX_CARDS_PER_SPEAKER_ROLE:
                    rejected["同角色超過上限"] += 1
                    continue
                speaker_key = _normalize_speaker_name(card.speaker)
                if cards_per_speaker[speaker_key] >= MAX_CARDS_PER_SPEAKER:
                    rejected["同一人超過上限"] += 1
                    continue
                cards_per_speaker_role[card.speaker_role] += 1
                cards_per_speaker[speaker_key] += 1
            seen_quotes.add(q_key)
            cards.append(card)
            cards_per_domain[domain] += 1

        met = _is_met(role, cards)

    stats["pages"] = pages_read
    stats["chats"] = model_calls
    stats["rejected"] = dict(rejected)
    print(
        f"[Teams] {role}（{subject}）：{len(cards)} 張卡"
        f"（讀 {pages_read} 頁／叫模型 {model_calls} 次／退回 {sum(rejected.values())} 張／達標={'是' if met else '否'}）"
    )
    return TeamReport(role=role, cards=cards, met=met, stats=stats, model=model_name, titles=titles)


# ---------------------------------------------------------------------------
# 多組執行與 ISO 週次快取
# ---------------------------------------------------------------------------

def _iso_week(today: date | None = None) -> str:
    iso_year, iso_week, _ = (today or date.today()).isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _cache_path(cache_dir: Path | None, subject: str, taiwan_query: str, role: str) -> Path:
    base = cache_dir or CACHE_DIR
    slug = re.sub(r"[^\w\-]+", "_", subject.strip(), flags=re.UNICODE).strip("_") or "subject"
    digest = hashlib.sha1(f"{subject.strip().lower()}|{taiwan_query.strip()}".encode("utf-8")).hexdigest()[:8]
    return base / f"{slug[:40]}_{digest}" / f"{_iso_week()}_{role}.json"


def _load_cached(path: Path) -> TeamReport | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cards = [TeamCard(**c) for c in data.get("cards", [])]
        return TeamReport(
            role=str(data["role"]),
            cards=cards,
            met=bool(data.get("met")),
            stats=dict(data.get("stats") or {}),
            model=str(data.get("model") or ""),
            titles=dict(data.get("titles") or {}),
        )
    except Exception:  # noqa: BLE001
        return None


def run_teams(
    subject: str,
    *,
    roles: Sequence[str] = ROLES,
    cache_dir: Path | None = None,
    **injected: Any,
) -> dict[str, TeamReport]:
    """依 subject＋ISO 週次快取並執行指定調研角色。"""
    tw_query = str(injected.get("taiwan_query") or "")
    reports: dict[str, TeamReport] = {}
    for role in roles:
        if role not in ROLES:
            raise ValueError(f"未知角色：{role}")
        cpath = _cache_path(cache_dir, subject, tw_query, role)
        cached = _load_cached(cpath)
        if cached is not None:
            print(f"[Teams] 用快取的 {role} 結果（{len(cached.cards)} 張證據卡）")
            reports[role] = cached
            continue
        report = run_role(role, subject, **injected)
        reports[role] = report
        if not report.stats.get("search_failed"):
            cpath.parent.mkdir(parents=True, exist_ok=True)
            cpath.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    return reports


# ---------------------------------------------------------------------------
# 給寫手的 Markdown 與研究來源轉換
# ---------------------------------------------------------------------------

def _round_robin_by_domain(cards: list[TeamCard]) -> list[TeamCard]:
    by_domain: dict[str, list[TeamCard]] = {}
    for c in cards:
        by_domain.setdefault(c.domain or "unknown", []).append(c)
    out: list[TeamCard] = []
    i = 0
    while any(i < len(v) for v in by_domain.values()):
        for v in by_domain.values():
            if i < len(v):
                out.append(v[i])
        i += 1
    return out


def _diversity_key(card: TeamCard) -> str:
    if card.role == "voices":
        return card.speaker_role or card.speaker or card.domain
    if card.role == "timeline":
        return _year_of(card.date) or card.date or card.domain
    return card.severity or card.domain


def _diversify(bucket: list[TeamCard]) -> list[TeamCard]:
    by_facet: dict[str, list[TeamCard]] = {}
    for c in bucket:
        by_facet.setdefault(_diversity_key(c), []).append(c)
    groups = [_round_robin_by_domain(v) for v in by_facet.values()]
    out: list[TeamCard] = []
    i = 0
    while any(i < len(g) for g in groups):
        for g in groups:
            if i < len(g):
                out.append(g[i])
        i += 1
    return out


def _pick_writer_cards(cards: list[TeamCard], limit: int = MAX_CARDS_FOR_WRITER) -> list[TeamCard]:
    """每個角色最多 8 張：先放 A/B 級，同等級再依角色多樣性排。"""
    out: list[TeamCard] = []
    for t in ("A", "B", "C", "D"):
        bucket = [c for c in cards if (c.tier or "C").upper() == t]
        if not bucket:
            continue
        out.extend(_diversify(bucket))
        if len(out) >= limit:
            break
    return out[:limit]


def _ordered_roles(reports: dict[str, TeamReport]) -> list[str]:
    ordered = [r for r in ROLES if r in reports]
    for r in reports:
        if r not in ordered:
            ordered.append(r)
    return ordered


def _card_line(c: TeamCard) -> str:
    if c.role == "voices":
        who = f"{c.speaker}（{c.speaker_role}）" if c.speaker_role else c.speaker
        claim_txt = c.claim
    elif c.role == "timeline":
        who = c.date
        claim_txt = f"{c.event}：{c.claim}" if c.event and c.event not in c.claim else c.claim
    else:
        who = c.severity or "風險"
        claim_txt = c.claim
    return f"- {claim_txt}｜“{c.quote}”｜{who}｜{c.url}（{c.tier} 級）"


def teams_md(reports: dict[str, TeamReport] | None) -> str:
    """輸出給寫手的中文調研區塊。"""
    if not reports:
        return ""
    parts = ["\n\n===== 調研小組（程式驗證過引句與出處）====="]
    for role in _ordered_roles(reports):
        report = reports[role]
        parts.append(f"## {ROLE_TITLES.get(role, role)}")
        if not report.met:
            parts.append("（這組證據不足，不要硬寫這一塊）")
        for card in _pick_writer_cards(report.cards, MAX_CARDS_FOR_WRITER):
            parts.append(_card_line(card))
    return "\n".join(parts)


def as_research_sources(reports: dict[str, TeamReport] | None) -> list[dict]:
    """每個網址一筆 ResearchSource（比照 filings_desk.as_research_sources）。"""
    if not reports:
        return []
    from substack_radar.editorial_research import _canonical_url
    from substack_radar.source_tier import tier as tier_of

    by_url: dict[str, dict[str, Any]] = {}
    for role in _ordered_roles(reports):
        report = reports[role]
        if not report or not report.cards:
            continue
        titles = getattr(report, "titles", None) or {}
        for card in report.cards:
            raw_url = (card.url or "").strip()
            if not raw_url:
                continue
            key = _canonical_url(raw_url) or raw_url
            entry = by_url.setdefault(
                key,
                {
                    "url": raw_url,
                    "domain": card.domain or _domain_of(raw_url),
                    "tier": card.tier or tier_of(raw_url),
                    "title": titles.get(raw_url) or "",
                    "roles": set(),
                    "cards": [],
                },
            )
            entry["roles"].add(card.role or role)
            entry["cards"].append(card)
            if not entry["title"] and titles.get(raw_url):
                entry["title"] = titles[raw_url]

    tier_order = {"A": 0, "B": 1, "C": 2, "D": 3}
    ordered_entries = sorted(by_url.values(), key=lambda e: tier_order.get(e["tier"], 2))

    out: list[dict] = []
    for entry in ordered_entries:
        cards = entry["cards"]
        quotes = list(dict.fromkeys(c.quote for c in cards if c.quote))
        excerpt = "\n".join(quotes)[:2600]
        if len(excerpt) < 120:
            excerpt = "\n".join(
                dict.fromkeys(
                    f"{ROLE_TITLES.get(c.role, c.role)}｜{c.claim}｜“{c.quote}”"
                    for c in cards if c.quote
                )
            )[:2600]
        if len(excerpt) < 120:
            continue
        title = re.sub(r"\s+", " ", entry["title"] or f"{cards[0].claim}（{entry['domain']}）").strip()[:240]
        if len(title) < 3:
            title = f"{entry['domain']} 報導"
        publisher = (entry["domain"] or "unknown publisher")[:160]
        if len(publisher) < 2:
            publisher = "unknown"
        if entry["tier"] == "A":
            evidence_role = "official"
        elif "counter" in entry["roles"]:
            evidence_role = "countercase"
        else:
            evidence_role = "analysis"
        out.append(
            {
                "title": title,
                "url": entry["url"],
                "publisher": publisher,
                "excerpt": excerpt,
                "evidence_role": evidence_role,
            }
        )
    return out
