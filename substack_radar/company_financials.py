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


def _cn(b, cur: str) -> str:
    """把『B（十億）』換成中文單位，杜絕 LLM 把 4490.91B 誤寫成 4490 億（差 10 倍）。
    1B = 10 億、1000B = 1 兆。"""
    if not isinstance(b, (int, float)):
        return "N/A"
    if abs(b) >= 1000:
        return f"{b / 1000:.2f} 兆{cur}"
    return f"{b * 10:.0f} 億{cur}"


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


    # --- 分析師共識 EPS vs 實際 EPS ----------------------------------
    # yfinance 的 earnings_history 給的是**賣方分析師共識 EPS** 與實際 EPS，
    # 不是公司自己在法說會給的財測。這個註解與下方 prompt 原本都寫成
    # 「管理層信用／管理層的說法可不可信」，2026-08-18 的瑞昱稿就照著這個
    # 標籤寫出「管理層指引連續四季落空、誠信度打折」——整段論證與一條
    # 證偽條件都建在誤讀上。標籤錯，寫手就會錯，而且每篇公司分析都會錯。
    # 資料來自既有相依 yfinance，無新增第三方程式碼。
    try:
        eh = t.earnings_history
        if eh is not None and not eh.empty:
            rows, beats = [], 0
            for idx, r in eh.iterrows():
                # 不能用 _b()——它是「換算成十億」的 helper，EPS 8.42 會變成 0.0，
                # 導致 beat 判定全部失真（實測四季全被誤判為達標）。
                def _f(v):
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        return None
                act, est = _f(r.get("epsActual")), _f(r.get("epsEstimate"))
                if act is None or est is None:
                    continue
                hit = act >= est
                beats += 1 if hit else 0
                rows.append({
                    "quarter": str(idx)[:10],
                    "actual": act, "estimate": est,
                    "surprise_pct": _f(r.get("surprisePercent")),
                    "beat": hit,
                })
            if rows:
                out["earnings_track_record"] = rows
                out["earnings_beat_count"] = f"{beats}/{len(rows)}"
    except Exception:
        pass

    # --- 宏觀情境背景 ------------------------------------------------
    # 公司分析不能只看公司。利率決定融資成本與估值折現率，波動率決定
    # 風險偏好，美元指數影響跨國收入。這三個是最小可用組合，都用 yfinance
    # 既有管道抓，不引入新來源。
    try:
        macro = {}
        for label, sym in (("us_10y_yield", "^TNX"), ("vix", "^VIX"), ("dxy", "DX-Y.NYB")):
            h = yf.Ticker(sym).history(period="5d")
            if h is not None and not h.empty:
                macro[label] = round(float(h["Close"].iloc[-1]), 2)
        if macro:
            out["macro_context"] = macro
    except Exception:
        pass

    return out, _format_md(out)


def _format_md(d: dict) -> str:
    cur = d.get("currency", "USD")
    L = [
        f"## 📊 {d.get('name')} ({d.get('ticker')}) 財報事實",
        "（來源：yfinance；本區所有數字為唯一可信來源，分析時一律以此為準，缺漏標 N/A，禁止自行編造或用記憶中的舊數字）",
        "（**單位換算鐵則**：B = 十億美元。寫中文時 1B=10 億、1000B=1 兆；例 4490.91B = 4.49 兆，**不是** 4490 億。"
        "市值與營收下面已附好中文單位，照抄即可、別自己換算。）",
        f"- 幣別：{cur}",
        f"- 市值：{_cn(d.get('market_cap_b'), cur)}（{d.get('market_cap_b','N/A')}B {cur}）｜P/E(trailing)：{d.get('trailing_pe','N/A')}"
        f"｜P/E(forward)：{d.get('forward_pe','N/A')}｜P/S：{d.get('ps','N/A')}",
        f"- 毛利率：{_pct(d.get('gross_margin'))}｜營益率：{_pct(d.get('op_margin'))}"
        f"｜淨利率：{_pct(d.get('profit_margin'))}｜ROE：{_pct(d.get('roe'))}｜ROA：{_pct(d.get('roa'))}",
        f"- 產業：{d.get('sector','?')} / {d.get('industry','?')}",
    ]
    if d.get("years"):
        L.append(f"- 年度（新→舊）：{d['years']}")
        if d.get("revenue_b"):
            rev_cn = "、".join(_cn(v, cur) for v in d["revenue_b"])
            # 標籤原本寫「近 {年度數} 年 CAGR」。4 個年度資料點（2022→2025）
            # 只跨 3 年，rev_cagr 本身也是用 n = 資料點數-1 算的——算式對、
            # 標籤錯，於是 2026-08-18 的聯發科稿照抄成「過去四年的複合年成長率」。
            # 跟「管理層信用」那次同一類：標籤錯，每一篇都會錯。
            _yrs = d["years"]
            _span = f"{_yrs[-1]}→{_yrs[0]}（{len(_yrs) - 1} 年）" if len(_yrs) > 1 else "N/A"
            L.append(f"- 營收（新→舊）：{rev_cn}（原值 {d['revenue_b']}B；"
                     f"{_span} CAGR≈{d.get('rev_cagr','N/A')}%）")
        if d.get("op_income_b"):
            # 這兩行原本只丟原始 B 值。營收與市值都經過 _cn() 換成中文單位，
            # 只有這裡沒有——於是 2026-08-18 的瑞昱稿把 14.39B 直接寫成
            # 「14.39 億」（正確 143.9 億，差 10 倍），而同一篇的營收 1227 億
            # 反而寫對了。差別就在這一行有沒有先換算好。
            L.append("- 營業利益（新→舊）：" + "、".join(
                _cn(v, cur) for v in d["op_income_b"]
            ) + f"（原值 {d['op_income_b']}B）")
        if d.get("op_margin_trend"):
            L.append(f"- 營益率趨勢（%，新→舊）：{d['op_margin_trend']}")
        if d.get("net_income_b"):
            L.append("- 淨利（新→舊）：" + "、".join(
                _cn(v, cur) for v in d["net_income_b"]
            ) + f"（原值 {d['net_income_b']}B）")
    if d.get("price") is not None:
        L.append(
            f"- 股價：{d['price']}（52 週區間 {d.get('range_52w','N/A')}；"
            f"距歷史高回落 {d.get('drawdown_from_ath','N/A')}%）"
        )
    if d.get("summary"):
        L.append(f"- 業務簡述（yfinance）：{d['summary']}")
    track = d.get("earnings_track_record") or []
    if track:
        beats = d.get("earnings_beat_count", "N/A")
        L.append("")
        L.append(f"### 分析師共識 EPS vs 實際 EPS（達標 {beats}）")
        L.append("（**這不是公司自己給的財測／法說會指引**，是 yfinance 的 "
                 "`earnings_dates`＝賣方分析師共識 EPS 與實際 EPS 的落差。"
                 "它回答的是「市場對這家公司的預期準不準」，不是「管理層說話算不算話」。"
                 "寫作時**不得**把它寫成「管理層指引落空」「管理層誠信度」——"
                 "2026-08-18 的瑞昱稿就是這樣寫錯，整段論證與一條證偽條件都建在誤讀上。"
                 "正確說法：連續低於共識代表分析師模型與實際脫節，可能是需求能見度低，"
                 "也可能是賣方過度樂觀，兩者都要另找證據才能斷。）")
        for r in track:
            mark = "達標" if r["beat"] else "未達標"
            sp = r.get("surprise_pct")
            sp_txt = f"｜偏離 {sp:+.1%}" if isinstance(sp, float) else ""
            L.append(f"- {r['quarter']}：實際 {r['actual']} vs 預估 {r['estimate']}　**{mark}**{sp_txt}")

    macro = d.get("macro_context") or {}
    if macro:
        L.append("")
        L.append("### 宏觀情境背景")
        L.append("（利率決定融資成本與折現率，波動率反映風險偏好，美元指數影響跨國收入。"
                 "情境分析請以這三個當期值為錨，不要用記憶中的舊數字。）")
        for k, label in (("us_10y_yield", "美國 10 年期公債殖利率"),
                         ("vix", "VIX 波動率指數"),
                         ("dxy", "美元指數 DXY")):
            if k in macro:
                L.append(f"- {label}：{macro[k]}")

    return "\n".join(L)


if __name__ == "__main__":
    import sys
    tk = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    data, md = fetch_financials(tk)
    print(md)
