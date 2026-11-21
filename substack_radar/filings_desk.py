"""文件組（2026-11-21 週末旗艦第 1 步）：美國公司的一手文件，直接從 SEC 拿。

省 token 的分工：
- 程式負責：查公司代碼、找最新 10-K／10-Q／財報新聞稿、拆章節，以及從 SEC 結構化資料（XBRL）拿財報數字。
  這些都不用模型。
- 便宜模型（閘道 fast 角色，免費）只負責「讀一段原文、交回證據卡」。寫手只看證據卡，不看原文。
- 程式驗證每張卡：引句要在原文一字找得到，卡上的數字要在引句裡。
- 結果依申報文件編號快取：週日下篇、下次再寫同一家公司都直接重用，不重跑。

SEC 結構化資料拿到的年度數字同時是「標準答案」，交給 probes.truth_probe 做隱藏抽查。
寫手不會知道哪些數字會被查。
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CACHE_DIR = REPO / "data" / "filings_cache"
UA = os.getenv("SEC_USER_AGENT", "news-radar-research/1.0 research-bot@example.org")
CHUNK = 7000
MAX_CHUNKS = {"business": 3, "risk": 3, "mdna": 3, "quarter": 2, "release": 2}
CARDS_PER_CHUNK = 5

CONCEPTS = {
    "營收": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
    "毛利": ("GrossProfit",),
    "營業利益": ("OperatingIncomeLoss",),
    "淨利": ("NetIncomeLoss",),
    "研發費用": ("ResearchAndDevelopmentExpense",),
    "營業現金流": ("NetCashProvidedByUsedInOperatingActivities",),
    "資本支出": ("PaymentsToAcquirePropertyPlantAndEquipment",),
}


@dataclass
class Card:
    claim: str
    quote: str
    number: str
    use: str
    doc: str          # 10-K Item 1／10-Q MD&A／財報新聞稿……
    url: str


@dataclass
class FilingsPack:
    ticker: str
    company: str = ""
    filings: dict = field(default_factory=dict)      # kind → {form, date, url}
    truth: dict = field(default_factory=dict)        # 營收 → [{"fy": 2026, "end": ..., "usd": ...}]
    cards: list = field(default_factory=list)
    rejected: int = 0
    model: str = ""


# ---------------------------------------------------------------------------
# SEC
# ---------------------------------------------------------------------------

def _get(url: str, *, as_json: bool = False):
    import httpx

    last = None
    for attempt in range(3):
        try:
            r = httpx.get(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"}, timeout=60,
                          follow_redirects=True)
            if r.status_code == 429:
                time.sleep(2 + attempt * 3)
                continue
            r.raise_for_status()
            time.sleep(0.15)                      # SEC 要求每秒不超過 10 次
            return r.json() if as_json else r.text
        except Exception as exc:                  # noqa: BLE001
            last = exc
            time.sleep(1 + attempt)
    raise RuntimeError(f"SEC 抓不到 {url[:90]}：{last}")


def cik_for(ticker: str, get=_get) -> tuple[int, str] | None:
    base = ticker.upper().split(".")[0].replace("-", ".")
    for row in (get("https://www.sec.gov/files/company_tickers.json", as_json=True) or {}).values():
        if str(row.get("ticker", "")).upper() == base:
            return int(row["cik_str"]), str(row.get("title", ""))
    return None


def _doc_url(cik: int, acc: str, primary: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{primary}"


def recent_filings(cik: int, get=_get) -> dict:
    """最新 10-K、最新 10-Q（比 10-K 新才要）、最新財報 8-K（Item 2.02）的新聞稿附件。"""
    recent = get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", as_json=True)["filings"]["recent"]
    out: dict = {}
    for i, form in enumerate(recent["form"]):
        acc, date_, primary = recent["accessionNumber"][i], recent["filingDate"][i], recent["primaryDocument"][i]
        if form in ("10-K", "20-F") and "annual" not in out:
            out["annual"] = {"form": form, "date": date_, "acc": acc, "url": _doc_url(cik, acc, primary)}
        elif form == "10-Q" and "quarter" not in out:
            out["quarter"] = {"form": form, "date": date_, "acc": acc, "url": _doc_url(cik, acc, primary)}
        elif form == "8-K" and "2.02" in str(recent["items"][i]) and "release" not in out:
            out["release"] = {"form": "8-K 財報新聞稿", "date": date_, "acc": acc, "index": (cik, acc)}
        if len(out) == 3:
            break
    if "quarter" in out and "annual" in out and out["quarter"]["date"] < out["annual"]["date"]:
        out.pop("quarter")
    rel = out.get("release")
    if rel:
        try:
            cik_, acc = rel.pop("index")
            idx = get(f"https://www.sec.gov/Archives/edgar/data/{cik_}/{acc.replace('-', '')}/index.json",
                      as_json=True)
            names = [it["name"] for it in idx["directory"]["item"]]
            ex = next((n for n in names if re.search(r"ex-?99", n, re.I) and n.lower().endswith((".htm", ".html"))),
                      None)
            if ex:
                rel["url"] = _doc_url(cik_, acc, ex)
            else:
                out.pop("release")
        except Exception:                          # noqa: BLE001
            out.pop("release", None)
    return out


def truth_table(cik: int, get=_get, years: int = 3) -> dict:
    """XBRL 年度數字（只取 10-K、期間約一年）。同一個期間取最後申報的那筆（重編後的數字）。"""
    facts = get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json", as_json=True)
    gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    out: dict = {}
    for label, names in CONCEPTS.items():
        by_end: dict = {}
        for name in names:
            for row in ((gaap.get(name) or {}).get("units") or {}).get("USD", []):
                if row.get("form") not in ("10-K", "20-F") or not row.get("start"):
                    continue
                days = (_d(row["end"]) - _d(row["start"])).days
                if not 330 <= days <= 400:
                    continue
                prev = by_end.get(row["end"])
                if prev is None or row.get("filed", "") > prev.get("filed", ""):
                    by_end[row["end"]] = row
            if by_end:
                break                              # 用第一個有資料的科目，不混用
        rows = sorted(by_end.values(), key=lambda r: r["end"], reverse=True)[:years]
        if rows:
            out[label] = [{"fy": int(r["end"][:4]), "end": r["end"], "usd": float(r["val"])} for r in rows]
    return out


def _d(s: str):
    from datetime import date
    return date.fromisoformat(s)


def _yi(usd: float) -> str:
    return f"{usd / 1e8:,.1f} 億美元"


def truth_md(truth: dict, url: str = "") -> str:
    if not truth:
        return ""
    lines = ["## SEC 年報數字（A 級，XBRL 結構化資料，期間＝會計年度結束日）"]
    for label, rows in truth.items():
        lines.append(f"- {label}：" + "；".join(f"FY{r['fy']}（{r['end']} 止）{_yi(r['usd'])}" for r in rows))
    rev = {r["end"]: r["usd"] for r in truth.get("營收", [])}
    gp = {r["end"]: r["usd"] for r in truth.get("毛利", [])}
    ratios = [f"FY{e[:4]} {gp[e] / rev[e] * 100:.1f}%" for e in sorted(rev, reverse=True) if e in gp and rev[e]]
    if ratios:
        lines.append("- 毛利率（毛利÷營收，程式計算）：" + "；".join(ratios))
    if url:
        lines.append(f"- 出處：{url}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 章節與證據卡
# ---------------------------------------------------------------------------

_HEAD = {
    "business": r"item\s*1\.?\s*business",
    "risk": r"item\s*1a\.?\s*risk\s*factors",
    "mdna": r"item\s*7\.?\s*management.{0,5}s\s*discussion",
    "after_mdna": r"item\s*7a\.?|item\s*8\.?\s*financial\s*statements",
    "after_business": r"item\s*1a\.?\s*risk\s*factors",
    "after_risk": r"item\s*1b\.?|item\s*1c\.?|item\s*2\.?\s*properties",
    "quarter": r"item\s*2\.?\s*management.{0,5}s\s*discussion",
    "after_quarter": r"item\s*3\.?\s*quantitative|item\s*4\.?\s*controls",
}


def _section(text: str, start_pat: str, end_pat: str) -> str:
    """目錄也會出現章節名：每個起點都試，取到下一個章節最長的那段。"""
    best = ""
    for m in re.finditer(start_pat, text, re.I):
        end = re.search(end_pat, text[m.end():], re.I)
        body = text[m.end(): m.end() + end.start()] if end else text[m.end(): m.end() + 60000]
        if len(body) > len(best):
            best = body
    return best.strip()


def sections(kind: str, text: str) -> dict[str, str]:
    if kind == "annual":
        return {"business": _section(text, _HEAD["business"], _HEAD["after_business"]),
                "risk": _section(text, _HEAD["risk"], _HEAD["after_risk"]),
                "mdna": _section(text, _HEAD["mdna"], _HEAD["after_mdna"])}
    if kind == "quarter":
        return {"quarter": _section(text, _HEAD["quarter"], _HEAD["after_quarter"])}
    return {"release": text}


DOC_NAME = {"business": "10-K 業務說明", "risk": "10-K 風險因素", "mdna": "10-K 管理層討論",
            "quarter": "10-Q 管理層討論", "release": "財報新聞稿"}

PROMPT = """你是研究助理，只讀下面這段美國公司申報文件原文，交回最多 {n} 張「證據卡」。
這篇文章要回答：{questions}

優先挑這幾種：錢怎麼流（收入從誰來、成本付給誰）、轉折點（策略改變、併購、新產品）、
依賴（大客戶、供應商、地區、單一產品）、風險（公司自己承認的）、跟台灣有關的（台灣、TSMC、Foxconn、供應鏈）。
不要挑法律套話和沒有資訊量的句子。

規則：
- 「引句」必須從原文一字不差地複製（英文原文，1～3 句，最多 400 字元）。
- 「主張」用一句繁體中文說這段引句證明了什麼，不能超出引句的內容。
- 「數字」填引句裡最關鍵的一個數字（照原文寫法，例如 $96.2 billion、25%），沒有就留空。
- 「用途」只能是：錢怎麼流／轉折／依賴／風險／台灣／其他。
- 只輸出 JSON：{{"cards": [{{"主張": "", "引句": "", "數字": "", "用途": ""}}]}}

=== 原文（{doc}）===
{chunk}
"""


def _chunks(text: str, limit: int) -> list[str]:
    paras, out, cur = re.split(r"\n+", text), [], ""
    for p in paras:
        if len(cur) + len(p) > CHUNK and cur:
            out.append(cur)
            cur = ""
        cur += p + "\n"
        if len(out) >= limit:
            break
    if cur and len(out) < limit:
        out.append(cur)
    return [c for c in out if len(c) > 400]


def verify_card(raw: dict, chunk: str) -> tuple[bool, str]:
    from substack_radar.fact_audit import _norm, coverage

    quote, number = str(raw.get("引句", "")).strip(), str(raw.get("數字", "")).strip()
    if not quote or not str(raw.get("主張", "")).strip():
        return False, "空白"
    if coverage(quote, _norm(chunk)) < 0.9:
        return False, "引句不在原文"
    if number:
        digits = re.sub(r"[^\d.]", "", number)
        if digits and digits not in re.sub(r"[^\d.]", "", quote.replace(",", "")):
            return False, "數字不在引句"
    return True, ""


def _default_chat(prompt: str) -> tuple[str, str]:
    from substack_radar.quality_loop import _LAST_USAGE, _oss_chat
    text = _oss_chat("fast", prompt, 600)
    return text, str(_LAST_USAGE.get("model") or "fast")


def extract_cards(doc_key: str, text: str, url: str, questions: str, chat=None) -> tuple[list[Card], int, str]:
    from substack_radar.fact_audit import loads_lenient

    chat = chat or _default_chat
    cards, rejected, model = [], 0, ""
    for chunk in _chunks(text, MAX_CHUNKS.get(doc_key, 2)):
        try:
            raw, model = chat(PROMPT.format(n=CARDS_PER_CHUNK, questions=questions or "這家公司怎麼賺錢、靠什麼贏、弱點在哪",
                                            doc=DOC_NAME[doc_key], chunk=chunk))
        except Exception as exc:                   # noqa: BLE001
            print(f"[Filings] ⚠️ {DOC_NAME[doc_key]} 讀卡失敗：{str(exc)[:100]}")
            continue
        data = loads_lenient(raw) or {}
        for item in (data.get("cards") if isinstance(data, dict) else None) or []:
            if not isinstance(item, dict):
                continue
            ok, _why = verify_card(item, chunk)
            if not ok:
                rejected += 1
                continue
            cards.append(Card(claim=str(item["主張"]).strip(), quote=str(item["引句"]).strip()[:400],
                              number=str(item.get("數字", "")).strip(), use=str(item.get("用途", "其他")).strip(),
                              doc=DOC_NAME[doc_key], url=url))
    return cards, rejected, model


# ---------------------------------------------------------------------------
# 主流程（含快取）
# ---------------------------------------------------------------------------

def build_pack(ticker: str, questions: str = "", *, get=_get, chat=None, cache_dir: Path | None = None) -> FilingsPack | None:
    from substack_radar.fix_desk import page_text

    found = cik_for(ticker, get)
    if not found:
        print(f"[Filings] {ticker} 不在 SEC 名單（非美國申報公司），略過文件組")
        return None
    cik, company = found
    filings = recent_filings(cik, get)
    key = "_".join(f.get("acc", "") for f in filings.values())
    cache = (cache_dir or CACHE_DIR) / ticker.upper() / f"{key or 'none'}.json"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        print(f"[Filings] 用快取的文件組結果（{len(data.get('cards', []))} 張證據卡）")
        return FilingsPack(**{**data, "cards": [Card(**c) for c in data.get("cards", [])]})

    pack = FilingsPack(ticker=ticker.upper(), company=company,
                       filings={k: {kk: v[kk] for kk in ("form", "date", "url") if kk in v} for k, v in filings.items()})
    try:
        pack.truth = truth_table(cik, get)
    except Exception as exc:                       # noqa: BLE001
        print(f"[Filings] ⚠️ XBRL 數字沒拿到：{str(exc)[:100]}")
    for kind, meta in filings.items():
        if not meta.get("url"):
            continue
        try:
            text = page_text(get(meta["url"]))
        except Exception as exc:                   # noqa: BLE001
            print(f"[Filings] ⚠️ {meta['form']} 抓不到：{str(exc)[:100]}")
            continue
        for doc_key, body in sections(kind, text).items():
            if len(body) < 800:
                print(f"[Filings] ⚠️ {DOC_NAME[doc_key]} 切不出內容，略過")
                continue
            cards, rej, model = extract_cards(doc_key, body, meta["url"], questions, chat)
            pack.cards += cards
            pack.rejected += rej
            pack.model = pack.model or model
    print(f"[Filings] {company}：{len(pack.cards)} 張證據卡（退回 {pack.rejected} 張），"
          f"XBRL 科目 {len(pack.truth)} 個")
    if pack.cards or pack.truth:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({**asdict(pack)}, ensure_ascii=False, indent=2), encoding="utf-8")
    return pack


MAX_CARDS_FOR_WRITER = 36


def pick_cards(cards: list, limit: int = MAX_CARDS_FOR_WRITER) -> list:
    """寫手只看 36 張：各用途輪流挑，不讓同一類（例如風險套話）佔滿。"""
    by_use: dict = {}
    for c in cards:
        by_use.setdefault(c.use, []).append(c)
    out, i = [], 0
    while len(out) < limit and any(i < len(v) for v in by_use.values()):
        for v in by_use.values():
            if i < len(v) and len(out) < limit:
                out.append(v[i])
        i += 1
    return out


def pack_md(pack: FilingsPack | None) -> str:
    if not pack:
        return ""
    parts = ["\n\n===== 一手文件（A 級，SEC 申報，程式驗證過引句）====="]
    annual = pack.filings.get("annual", {})
    parts.append(truth_md(pack.truth, annual.get("url", "")))
    if pack.cards:
        parts.append("## 證據卡（主張｜原文引句｜出處）")
        for c in pick_cards(pack.cards):
            parts.append(f"- [{c.use}] {c.claim}｜“{c.quote}”｜{c.doc} {c.url}")
    return "\n".join(p for p in parts if p)


def as_research_sources(pack: FilingsPack | None) -> list[dict]:
    """每份文件一筆 ResearchSource（A 級排在研究包最前面，出處清單也會列出）。"""
    if not pack:
        return []
    out = []
    for kind, meta in pack.filings.items():
        url = meta.get("url")
        quotes = [c.quote for c in pack.cards if c.url == url]
        if kind == "annual" and pack.truth:
            quotes.insert(0, truth_md(pack.truth))
        excerpt = "\n".join(quotes)[:2600]
        if not url or len(excerpt) < 120:
            continue
        out.append({"title": f"{pack.company} {meta['form']}（{meta['date']}）", "url": url,
                    "publisher": "SEC EDGAR", "excerpt": excerpt, "evidence_role": "official"})
    return out
