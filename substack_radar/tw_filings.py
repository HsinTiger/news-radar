"""台股文件組（WO-1）：台灣上市櫃公司的一手資料，直接從證交所／櫃買中心公開接口拿。

跟 filings_desk.py（美國公司）一樣的分工，但台股這邊全程不用語言模型，零 token：
- 月營收、綜合損益表都是結構化 JSON，每列一家公司，金額單位是「仟元」新台幣。
- 證據卡（Card）由程式直接產生：claim 是程式組的中文句子，quote 是那一列的原始欄位。
- 損益數字同時是「標準答案」，放進 truth 交給 probes.truth_probe 抽查。
  truth 的 "usd" 一律填 None、台幣填 "twd"：美元比對不會誤用台幣。
- 結果快取在 data/filings_cache/{ticker}/{資料年月}_{年度}{季別}.json。

資料類別（FilingsPack、Card）重用 filings_desk，不另外定義。
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict
from pathlib import Path

from substack_radar.filings_desk import Card, FilingsPack

REPO = Path(__file__).resolve().parents[1]
CACHE_DIR = REPO / "data" / "filings_cache"
UA = os.getenv("TW_FILINGS_USER_AGENT", "Mozilla/5.0 news-radar-research")
CACHE_TTL_HOURS = 12          # 快取檔在這段時間內視為新鮮，直接用，連接口都不打

USE = "錢怎麼流"

TWSE_MONTHLY = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
TPEX_MONTHLY = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"
TWSE_INCOME = "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_{kind}"
TPEX_INCOME = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_{kind}"
INCOME_KINDS = ("ci", "basi", "bd", "fh", "ins", "mim")     # 一般業、銀行、證券、金控、保險、異業

# 各市場的名稱與出處
MARKETS = {
    "TW": {"monthly": TWSE_MONTHLY, "income": TWSE_INCOME, "doc": "證交所月營收",
           "publisher": "臺灣證券交易所", "name": "上市"},
    "TWO": {"monthly": TPEX_MONTHLY, "income": TPEX_INCOME, "doc": "櫃買中心月營收",
            "publisher": "櫃買中心", "name": "上櫃"},
}

# 欄位別名：證交所用中文欄位；櫃買中心損益表的部分欄位是英文（Date、Year、Season、SecuritiesCompanyCode、
# CompanyName），金融業別又有幾種不同寫法，所以每個欄位都列出已知的名字，依序找第一個有值的。
CODE_KEYS = ("公司代號", "SecuritiesCompanyCode", "Code")
NAME_KEYS = ("公司名稱", "CompanyName")
DATE_KEYS = ("出表日期", "Date")
YM_KEYS = ("資料年月",)
YEAR_KEYS = ("年度", "Year")
SEASON_KEYS = ("季別", "Season")
SUBJECTS = {
    "營收": ("營業收入", "收益", "收入", "淨收益"),
    "毛利": ("營業毛利（毛損）淨額", "營業毛利（毛損）"),
    "營業利益": ("營業利益（損失）", "營業利益"),
    "淨利": ("淨利（淨損）歸屬於母公司業主", "本期淨利（淨損）", "本期稅後淨利（淨損）"),   # 跟新聞、法說會同口徑
}
M_REV = "營業收入-當月營收"
M_REV_LY = "營業收入-去年當月營收"
M_YOY = "營業收入-去年同月增減(%)"
M_CUM = "累計營業收入-當月累計營收"
M_CUM_LY = "累計營業收入-去年累計營收"
M_CUM_YOY = "累計營業收入-前期比較增減(%)"


# ---------------------------------------------------------------------------
# 抓取與欄位處理
# ---------------------------------------------------------------------------

def _get(url: str, *, as_json: bool = True):
    import httpx

    last = None
    for attempt in range(3):
        try:
            r = httpx.get(url, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=60,
                          follow_redirects=True)
            if r.status_code == 429:
                time.sleep(2 + attempt * 3)
                continue
            r.raise_for_status()
            return r.json() if as_json else r.text
        except Exception as exc:                  # noqa: BLE001
            last = exc
            time.sleep(1 + attempt)
    raise RuntimeError(f"證交所／櫃買中心抓不到 {url[:90]}：{last}")


def _first(row: dict, keys: tuple) -> str:
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _num(v) -> float | None:
    s = re.sub(r"[,\s]", "", str(v if v is not None else ""))
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def roc_date(s: str) -> str:
    """民國日期 1151121 → 2026-11-21；格式不對回空字串。"""
    d = re.sub(r"\D", "", str(s or ""))
    if len(d) not in (6, 7):
        return ""
    return f"{int(d[:-4]) + 1911:04d}-{d[-4:-2]}-{d[-2:]}"


def roc_ym(s: str) -> tuple[int, int] | None:
    """民國年月 11510 → (2026, 10)。"""
    d = re.sub(r"\D", "", str(s or ""))
    if len(d) not in (4, 5) or not 1 <= int(d[-2:]) <= 12:
        return None
    return int(d[:-2]) + 1911, int(d[-2:])


def _yi(twd: float) -> str:
    return f"{twd / 1e8:,.1f} 億元"


def _split_ticker(ticker: str) -> tuple[str, str] | None:
    code, _, suffix = ticker.strip().upper().partition(".")
    if code and suffix in MARKETS:
        return code, suffix
    return None


def _find(rows, code: str) -> dict | None:
    for row in rows or []:
        if isinstance(row, dict) and _first(row, CODE_KEYS) == code:
            return row
    return None


def _fetch(get, url: str):
    try:
        data = get(url, as_json=True)
    except Exception as exc:                       # noqa: BLE001
        print(f"[台股文件組] ⚠️ 接口抓不到：{url}（{str(exc)[:80]}）")
        return None
    return data if isinstance(data, list) else None


def find_income(code: str, market: dict, get) -> tuple[dict, str] | None:
    """一般業找不到就依序試銀行、證券、金控、保險、異業。"""
    for kind in INCOME_KINDS:
        url = market["income"].format(kind=kind)
        row = _find(_fetch(get, url), code)
        if row:
            return row, url
    return None


# ---------------------------------------------------------------------------
# 證據、標準答案
# ---------------------------------------------------------------------------

def _q(row: dict, *keys: str) -> str:
    """原始欄位字串「欄位名: 值」，多個用「；」連起來；沒有值的欄位略過。"""
    return "；".join(f"{k}: {row[k]}" for k in keys if str(row.get(k, "")).strip())


def _signed(pct: float) -> str:
    return f"年增 {pct:,.1f}%" if pct >= 0 else f"年減 {abs(pct):,.1f}%"


def monthly_cards(row: dict, doc: str, url: str) -> list[Card]:
    """最近一個月營收、年增率、累計年增率，各一張卡，程式組句，不經過模型。"""
    ym = roc_ym(_first(row, YM_KEYS))
    if not ym:
        return []
    y, m = ym
    rev, rev_ly, yoy = _num(row.get(M_REV)), _num(row.get(M_REV_LY)), _num(row.get(M_YOY))
    cum, cum_ly, cum_yoy = _num(row.get(M_CUM)), _num(row.get(M_CUM_LY)), _num(row.get(M_CUM_YOY))
    cards: list[Card] = []
    if rev is not None:
        tail = f"，{_signed(yoy)}" if yoy is not None else ""
        cards.append(Card(claim=f"{y} 年 {m} 月營收 {_yi(rev * 1000)}{tail}",
                          quote=_q(row, "資料年月", M_REV, M_YOY), number=_yi(rev * 1000),
                          use=USE, doc=doc, url=url))
    if yoy is not None:
        base = f"（去年同月 {_yi(rev_ly * 1000)}）" if rev_ly is not None else ""
        cards.append(Card(claim=f"{y} 年 {m} 月營收{_signed(yoy)}{base}",
                          quote=_q(row, "資料年月", M_REV, M_REV_LY, M_YOY), number=f"{yoy:,.1f}%",
                          use=USE, doc=doc, url=url))
    if cum_yoy is not None:
        amt = f" {_yi(cum * 1000)}" if cum is not None else ""
        base = f"（去年同期 {_yi(cum_ly * 1000)}）" if cum_ly is not None else ""
        span = f"1–{m} 月" if m > 1 else "1 月"
        cards.append(Card(claim=f"{y} 年{span}累計營收{amt}，累計{_signed(cum_yoy)}{base}",
                          quote=_q(row, "資料年月", M_CUM, M_CUM_LY, M_CUM_YOY), number=f"{cum_yoy:,.1f}%",
                          use=USE, doc=doc, url=url))
    return cards


def income_truth(row: dict) -> dict:
    """四個科目（仟元 → 元，民國 → 西元）。usd 一律 None，台幣放 twd。"""
    year, season = _num(_first(row, YEAR_KEYS)), _first(row, SEASON_KEYS)
    if year is None or not re.fullmatch(r"[1-4]", season):
        return {}
    fy = int(year) + 1911
    out: dict = {}
    for label, keys in SUBJECTS.items():
        for k in keys:
            v = _num(row.get(k))
            if v is not None:
                out[label] = [{"fy": fy, "end": f"{fy}Q{season}累計", "usd": None, "twd": v * 1000.0,
                               "currency": "TWD"}]
                break
    return out


def _ratio(truth: dict, num_label: str) -> str:
    rev, num = truth.get("營收"), truth.get(num_label)
    if rev and num and rev[0]["twd"] and rev[0]["end"] == num[0]["end"]:
        return f"{num[0]['twd'] / rev[0]['twd'] * 100:.1f}%"
    return ""


# ---------------------------------------------------------------------------
# 主流程（含快取）
# ---------------------------------------------------------------------------

def _load(path: Path) -> FilingsPack:
    data = json.loads(path.read_text(encoding="utf-8"))
    return FilingsPack(**{**data, "cards": [Card(**c) for c in data.get("cards", [])]})


def _fresh_cache(folder: Path, ttl_hours: float) -> Path | None:
    """目錄裡最新的快取檔，且在有效時間內才算；不用打接口就能用。"""
    files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if folder.is_dir() else []
    if files and time.time() - files[0].stat().st_mtime < ttl_hours * 3600:
        return files[0]
    return None


def build_pack(ticker: str, *, get=None, cache_dir: Path | None = None,
               ttl_hours: float = CACHE_TTL_HOURS) -> FilingsPack | None:
    split = _split_ticker(ticker)
    if not split:
        print(f"[台股文件組] {ticker} 不是 .TW／.TWO 結尾的台股代號，略過")
        return None
    code, suffix = split
    market = MARKETS[suffix]
    folder = (cache_dir or CACHE_DIR) / ticker.upper()

    hit = _fresh_cache(folder, ttl_hours)
    if hit:
        try:
            pack = _load(hit)
            print(f"[台股文件組] 用快取的文件組結果（{hit.name}，{len(pack.cards)} 張證據卡）")
            return pack
        except Exception as exc:                   # noqa: BLE001
            print(f"[台股文件組] ⚠️ 快取讀不了，重抓：{str(exc)[:80]}")

    get = get or _get
    row = _find(_fetch(get, market["monthly"]), code)
    if not row:
        print(f"[台股文件組] {ticker} 不在{market['name']}月營收名單，略過文件組")
        return None
    company = _first(row, NAME_KEYS)
    inc = find_income(code, market, get)

    ym = _first(row, YM_KEYS) or "none"
    period = f"{_first(inc[0], YEAR_KEYS)}{_first(inc[0], SEASON_KEYS)}" if inc else "none"
    cache = folder / f"{ym}_{period}.json"
    if cache.exists():
        try:
            pack = _load(cache)
            os.utime(cache)                        # 重新計時，有效期間內下次不用再打接口
            print(f"[台股文件組] 用快取的文件組結果（{cache.name}，{len(pack.cards)} 張證據卡）")
            return pack
        except Exception as exc:                   # noqa: BLE001
            print(f"[台股文件組] ⚠️ 快取讀不了，重算：{str(exc)[:80]}")

    pack = FilingsPack(ticker=ticker.upper(), company=company, model="")
    pack.filings["monthly"] = {"form": "月營收", "date": roc_date(_first(row, DATE_KEYS)), "url": market["monthly"]}
    pack.cards = monthly_cards(row, market["doc"], market["monthly"])
    if not pack.cards:
        print(f"[台股文件組] ⚠️ {company} 月營收欄位解析不出卡片（資料年月或營收欄位缺漏）")
    if inc:
        pack.truth = income_truth(inc[0])
        pack.filings["income"] = {"form": "綜合損益表", "date": roc_date(_first(inc[0], DATE_KEYS)), "url": inc[1]}
    else:
        print(f"[台股文件組] ⚠️ {company} 找不到綜合損益表（試過 {'、'.join(INCOME_KINDS)}），只有月營收")
    print(f"[台股文件組] {company}：{len(pack.cards)} 張證據卡，損益科目 {len(pack.truth)} 個（零 token）")
    if pack.cards or pack.truth:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(asdict(pack), ensure_ascii=False, indent=2), encoding="utf-8")
    return pack


# ---------------------------------------------------------------------------
# 輸出給寫手、研究包
# ---------------------------------------------------------------------------

def _market(pack: FilingsPack) -> dict:
    return MARKETS["TWO" if pack.ticker.upper().endswith(".TWO") else "TW"]


def truth_md(truth: dict, url: str = "") -> str:
    if not truth:
        return ""
    end = next(iter(truth.values()))[0]["end"]
    src = f"（出處：{url}）" if url else ""
    lines = [f"## 綜合損益表（A 級，{end}，單位換算成億元）"]
    for label, rows in truth.items():
        lines.append(f"- {label}：{_yi(rows[0]['twd'])}{src}")
    gm, om = _ratio(truth, "毛利"), _ratio(truth, "營業利益")
    if gm:
        lines.append(f"- 毛利率（毛利÷營收，程式計算）：{gm}")
    if om:
        lines.append(f"- 營益率（營業利益÷營收，程式計算）：{om}")
    return "\n".join(lines)


def pack_md(pack: FilingsPack | None) -> str:
    if not pack:
        return ""
    parts = ["\n\n===== 一手文件（A 級，證交所／櫃買中心公開資料）====="]
    parts.append(truth_md(pack.truth, pack.filings.get("income", {}).get("url", "")))
    if pack.cards:
        parts.append("## 月營收卡片（程式產生｜主張｜原始欄位｜出處）")
        for c in pack.cards:
            parts.append(f"- [{c.use}] {c.claim}｜{c.quote}｜{c.doc} {c.url}")
    srcs = [f"{meta['form']}：{meta['url']}" for meta in pack.filings.values() if meta.get("url")]
    if srcs:
        parts.append("## 出處網址\n" + "\n".join(f"- {s}" for s in srcs))
    return "\n".join(p for p in parts if p)


def as_research_sources(pack: FilingsPack | None) -> list[dict]:
    """每個接口一筆 ResearchSource（格式比照 filings_desk.as_research_sources）。"""
    if not pack:
        return []
    mk = _market(pack)
    out = []
    for kind, meta in pack.filings.items():
        url = meta.get("url")
        if kind == "monthly":
            lines = [f"{c.claim}（{c.quote}）" for c in pack.cards if c.url == url]
        else:
            lines = []
            for label, rows in pack.truth.items():
                lines.append(f"{label}：{_yi(rows[0]['twd'])}（{rows[0]['end']}，原始值 {rows[0]['twd'] / 1000:,.0f} 仟元）")
            for name, lab in (("毛利率", "毛利"), ("營益率", "營業利益")):
                if (r := _ratio(pack.truth, lab)):
                    lines.append(f"{name}（程式計算）：{r}")
            if lines:
                lines.insert(0, f"{pack.company} 綜合損益表，單位已換算成億元新台幣：")
        excerpt = "\n".join(lines)[:2600]
        if not url or len(excerpt) < 120:
            continue
        out.append({"title": f"{pack.company} {meta['form']}（{meta.get('date', '')}）", "url": url,
                    "publisher": mk["publisher"], "excerpt": excerpt, "evidence_role": "official"})
    return out
