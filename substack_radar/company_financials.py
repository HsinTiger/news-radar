"""每週公司營運分析用的財報數字來源（yfinance · 美股 + 台股 .TW）。

設計原則（2026-06-20 Hsin）：數字一律來自 yfinance，LLM 只負責「分析」、不負責「編數字」。
fetch_financials(ticker) 回傳 (data dict, markdown_block)；markdown 當「財報事實」餵進 compose，
缺資料標 N/A、絕不瞎掰。美股用裸 ticker（NVDA），台股用 .TW（2330.TW）。
"""
from __future__ import annotations
from typing import Optional, Tuple


def _b(v) -> Optional[float]:
    try:
        f = float(v)
        if f != f:  # NaN（yfinance 最舊年度常缺）
            return None
        return round(f / 1e9, 2)
    except Exception:
        return None


def _pct(x) -> str:
    return f"{x * 100:.0f}%" if isinstance(x, (int, float)) else "N/A"


def fetch_financials(ticker: str) -> Tuple[dict, str]:
    """抓一間公司的結構化財報 + 衍生指標。任何抓取失敗都 graceful，回 (部分 dict, markdown)。"""
    import yfinance as yf

    t = yf.Ticker(ticker)
    try:
        info = t.info or {}
    except Exception:
        info = {}
    cur = info.get("financialCurrency") or info.get("currency") or "USD"
    out: dict = {
        "ticker": ticker,
        "name": info.get("shortName") or info.get("longName") or ticker,
        "currency": cur,
        "market_cap_b": _b(info.get("marketCap")),
        "trailing_pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "ps": info.get("priceToSalesTrailing12Months"),
        "gross_margin": info.get("grossMargins"),
        "op_margin": info.get("operatingMargins"),
        "profit_margin": info.get("profitMargins"),
        "roe": info.get("returnOnEquity"),
        "roa": info.get("returnOnAssets"),
        "rev_growth": info.get("revenueGrowth"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "summary": (info.get("longBusinessSummary") or "")[:600],
    }

    # 多年度損益（yfinance 欄位 = 年度，新→舊）
    try:
        fin = t.financials
        if fin is not None and not fin.empty:
            years = [c.year for c in fin.columns]

            def _row(name):
                return [_b(v) for v in fin.loc[name].values] if name in fin.index else None

            rev = _row("Total Revenue")
            op = _row("Operating Income")
            ni = _row("Net Income")
            # 以「營收非 None」的年數為準，把所有序列切齊（丟掉最舊那個常缺的年）。
            valid = len([r for r in (rev or []) if r is not None]) if rev else len(years)
            valid = max(valid, 1)
            out["years"] = years[:valid]
            out["revenue_b"] = rev[:valid] if rev else None
            out["op_income_b"] = op[:valid] if op else None
            out["net_income_b"] = ni[:valid] if ni else None
            if rev and valid >= 2 and rev[0] and rev[valid - 1]:
                try:
                    n = valid - 1
                    out["rev_cagr"] = round(((rev[0] / rev[valid - 1]) ** (1 / n) - 1) * 100, 1)
                except Exception:
                    pass
            if rev and op:
                out["op_margin_trend"] = [
                    round(float(o) / float(r) * 100, 1) if (o and r) else None
                    for o, r in zip(op[:valid], rev[:valid])
                ]
    except Exception:
        pass

    # 股價 / 距歷史高回落
    try:
        hist = t.history(period="max")
        if hist is not None and not hist.empty:
            ath = float(hist["Close"].max())
            last = float(hist["Close"].iloc[-1])
            out["price"] = round(last, 2)
            out["ath"] = round(ath, 2)
            out["drawdown_from_ath"] = round((last / ath - 1) * 100, 1) if ath else None
        h52 = t.history(period="1y")
        if h52 is not None and not h52.empty:
            out["range_52w"] = [round(float(h52["Close"].min()), 2), round(float(h52["Close"].max()), 2)]
    except Exception:
        pass

    return out, _format_md(out)


def _format_md(d: dict) -> str:
    cur = d.get("currency", "USD")
    L = [
        f"## 📊 {d.get('name')} ({d.get('ticker')}) 財報事實",
        "（來源：yfinance；本區所有數字為唯一可信來源，分析時一律以此為準，缺漏標 N/A，禁止自行編造或用記憶中的舊數字）",
        f"- 幣別：{cur}",
        f"- 市值：{d.get('market_cap_b','N/A')}B {cur}｜P/E(trailing)：{d.get('trailing_pe','N/A')}"
        f"｜P/E(forward)：{d.get('forward_pe','N/A')}｜P/S：{d.get('ps','N/A')}",
        f"- 毛利率：{_pct(d.get('gross_margin'))}｜營益率：{_pct(d.get('op_margin'))}"
        f"｜淨利率：{_pct(d.get('profit_margin'))}｜ROE：{_pct(d.get('roe'))}｜ROA：{_pct(d.get('roa'))}",
        f"- 產業：{d.get('sector','?')} / {d.get('industry','?')}",
    ]
    if d.get("years"):
        L.append(f"- 年度（新→舊）：{d['years']}")
        if d.get("revenue_b"):
            L.append(f"- 營收（B {cur}）：{d['revenue_b']}（近 {len(d['years'])} 年 CAGR≈{d.get('rev_cagr','N/A')}%）")
        if d.get("op_income_b"):
            L.append(f"- 營業利益（B）：{d['op_income_b']}")
        if d.get("op_margin_trend"):
            L.append(f"- 營益率趨勢（%，新→舊）：{d['op_margin_trend']}")
        if d.get("net_income_b"):
            L.append(f"- 淨利（B）：{d['net_income_b']}")
    if d.get("price") is not None:
        L.append(
            f"- 股價：{d['price']}（52 週區間 {d.get('range_52w','N/A')}；"
            f"距歷史高回落 {d.get('drawdown_from_ath','N/A')}%）"
        )
    if d.get("summary"):
        L.append(f"- 業務簡述（yfinance）：{d['summary']}")
    return "\n".join(L)


if __name__ == "__main__":
    import sys
    tk = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    data, md = fetch_financials(tk)
    print(md)
