"""幣圈事實表。跟 company_financials.fetch_financials 同介面，好讓 company 模式
（賺錢有道）整套規格與四道閘門原封不動沿用。

為什麼不能直接用 company_financials
------------------------------------
BTC/ETH 沒有營收、毛利、EPS。yfinance 對它們的 financials 是空的。硬跑 company
模式會拿到一份幾乎空白的事實表，然後寫手就會自己「補」——那正是我們花一整天
在防的事。

刻意寫進事實表的三件事
----------------------
1. **有什麼**：價格、市值、流通量／上限、52 週區間與回撤、市值佔比、算力（BTC）。
2. **每個數字的抓取時間**，因為幣價是 24 小時市場，「當時」比股票更重要。
3. **沒有什麼**：現貨 ETF 淨流量與 ETH 質押率目前沒有可信免費來源。
   明寫「沒有」比留白安全——留白會被寫手用記憶裡的舊數字填滿。

鏈上資料來源
------------
CoinMetrics community API（免 key、免費、資料到前一日）。第一版只抓了價格與
供給就下結論說「幣圈資料拿不到」，是查得太淺——owner 指出區塊鏈資料本來就透明。
實際上 MVRV、交易所淨流入／流出、活躍地址這些 CryptoQuant 類指標都是開放的。
""" 
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Tuple
from urllib.request import Request, urlopen

_CG = "https://api.coingecko.com/api/v3"
_COINS = {"BTC": "bitcoin", "ETH": "ethereum"}
_TIMEOUT = 20


def _get(url: str) -> dict | list | None:
    try:
        req = Request(url, headers={"User-Agent": "news-radar/1.0"})
        with urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _fmt_usd(v) -> str:
    if not isinstance(v, (int, float)):
        return "N/A"
    if abs(v) >= 1e12:
        return f"{v / 1e12:.2f} 兆美元"
    if abs(v) >= 1e8:
        return f"{v / 1e8:.1f} 億美元"
    return f"{v:,.0f} 美元"


_CM = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
_CM_METRICS = ("AdrActCnt,CapMVRVCur,CapMrktCurUSD,FlowInExUSD,FlowOutExUSD,"
               "HashRate,IssTotUSD,SplyCur,TxCnt")


def _fetch_onchain(sym: str) -> dict:
    """CoinMetrics community：免 key，資料到前一日。抓不到就回空 dict。"""
    d = _get(f"{_CM}?assets={sym.lower()}&metrics={_CM_METRICS}"
             f"&frequency=1d&page_size=2")
    rows = (d or {}).get("data") or []
    if not rows:
        return {}
    last = rows[-1]
    out = {"as_of": str(last.get("time", ""))[:10]}
    for key in ("AdrActCnt", "CapMVRVCur", "FlowInExUSD", "FlowOutExUSD",
                "IssTotUSD", "TxCnt", "SplyCur"):
        try:
            out[key] = float(last[key])
        except (KeyError, TypeError, ValueError):
            pass
    # flash＝初步值，之後可能修正。標出來，不要讓它看起來跟定稿一樣硬。
    out["flash"] = any(str(k).endswith("-status") and last[k] == "flash" for k in last)
    if "FlowInExUSD" in out and "FlowOutExUSD" in out:
        out["net_exchange_flow"] = out["FlowInExUSD"] - out["FlowOutExUSD"]
    return out


def fetch_crypto(symbol: str) -> Tuple[dict, str]:
    """回 (結構化 dict, markdown 事實表)。任何來源失敗都 graceful。"""
    sym = symbol.upper().replace("-USD", "").strip()
    coin = _COINS.get(sym)
    out: dict = {"symbol": sym, "coin_id": coin,
                 "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    import yfinance as yf

    info = {}
    try:
        info = yf.Ticker(f"{sym}-USD").info or {}
    except Exception:
        pass
    out["price"] = info.get("regularMarketPrice") or info.get("currentPrice")
    out["market_cap"] = info.get("marketCap")
    out["circulating_supply"] = info.get("circulatingSupply")
    out["max_supply"] = info.get("maxSupply") or None
    out["high_52w"] = info.get("fiftyTwoWeekHigh")
    out["low_52w"] = info.get("fiftyTwoWeekLow")
    out["volume_24h"] = info.get("volume24Hr")
    if out["price"] and out["high_52w"]:
        out["drawdown_from_52w_high"] = round(
            (out["price"] - out["high_52w"]) / out["high_52w"] * 100, 1)

    glob = _get(f"{_CG}/global")
    if isinstance(glob, dict) and glob.get("data"):
        d = glob["data"]
        out["total_crypto_mcap"] = (d.get("total_market_cap") or {}).get("usd")
        out["dominance_pct"] = (d.get("market_cap_percentage") or {}).get(sym.lower())

    out["onchain"] = _fetch_onchain(sym)

    if sym == "BTC":
        hr = _get("https://mempool.space/api/v1/mining/hashrate/3d")
        if isinstance(hr, dict) and hr.get("hashrates"):
            latest = hr["hashrates"][-1]
            out["hashrate_eh_s"] = round(float(latest["avgHashrate"]) / 1e18, 1)
            out["hashrate_as_of"] = datetime.fromtimestamp(
                latest["timestamp"], tz=timezone.utc).date().isoformat()
        if isinstance(hr, dict) and hr.get("currentDifficulty"):
            out["difficulty"] = hr["currentDifficulty"]

    return out, _format_md(out)


def _format_md(d: dict) -> str:
    sym = d.get("symbol", "?")
    L = [f"## {sym} 市場事實（抓取時間 {d.get('fetched_at')} UTC）",
         "（幣圈是 24 小時市場，下面每個數字都是上面那個時間點的快照。"
         "寫作時要說「截至撰稿時」，不要寫成長期成立的事實。）", ""]
    if d.get("price") is not None:
        L.append(f"- 價格：{d['price']:,.0f} 美元")
    if d.get("market_cap") is not None:
        L.append(f"- 市值：{_fmt_usd(d['market_cap'])}")
    if d.get("high_52w") and d.get("low_52w"):
        L.append(f"- 52 週區間：{d['low_52w']:,.0f} – {d['high_52w']:,.0f} 美元"
                 f"（距 52 週高點 {d.get('drawdown_from_52w_high', 'N/A')}%）")
    cs, ms = d.get("circulating_supply"), d.get("max_supply")
    if cs:
        line = f"- 流通量：{cs:,.0f} {sym}"
        if ms:
            line += f"（上限 {ms:,.0f}，已釋出 {cs / ms * 100:.1f}%）"
        else:
            line += "（無發行上限）"
        L.append(line)
    if d.get("dominance_pct") is not None:
        L.append(f"- 市值佔比：{d['dominance_pct']:.1f}%"
                 f"（全市場 {_fmt_usd(d.get('total_crypto_mcap'))}）")
    if d.get("volume_24h"):
        L.append(f"- 24 小時成交額：{_fmt_usd(d['volume_24h'])}")
    if d.get("hashrate_eh_s"):
        L.append(f"- 網路算力：{d['hashrate_eh_s']} EH/s（{d.get('hashrate_as_of')}）")

    oc = d.get("onchain") or {}
    if oc:
        L += ["", f"### 鏈上指標（CoinMetrics，{oc.get('as_of')}）"]
        if oc.get("flash"):
            L.append("（交易所流量標記為 flash＝初步值，之後可能被修正。"
                     "寫作時要說「初步數據」，不要當定稿數字用。）")
        if oc.get("CapMVRVCur") is not None:
            mvrv = oc["CapMVRVCur"]
            L.append(f"- MVRV：{mvrv:.2f}"
                     f"（市值 ÷ 實現市值。低於 1 代表平均持有者帳面虧損；"
                     f"這是歷史上的深度價值區，但不是買進訊號，過去也曾在 1 以下停留數月）")
        if oc.get("net_exchange_flow") is not None:
            net = oc["net_exchange_flow"]
            L.append(f"- 交易所淨流量：{'淨流出' if net < 0 else '淨流入'} "
                     f"{_fmt_usd(abs(net))}（流入 {_fmt_usd(oc.get('FlowInExUSD'))}／"
                     f"流出 {_fmt_usd(oc.get('FlowOutExUSD'))}）"
                     "。淨流出常被解讀為累積，但也可能只是託管方換倉，不可單獨當結論")
        if oc.get("AdrActCnt") is not None:
            L.append(f"- 單日活躍地址：{oc['AdrActCnt']:,.0f}")
        if oc.get("TxCnt") is not None:
            L.append(f"- 單日交易筆數：{oc['TxCnt']:,.0f}")
        if oc.get("IssTotUSD") is not None:
            L.append(f"- 單日新發行價值：{_fmt_usd(oc['IssTotUSD'])}"
                     "（礦工／驗證者每天必須吸收的賣壓上限）")

    L += ["", "### 這份事實表**沒有**的東西（不可自行補寫）",
          "（2026-08-19 實測，這幾項目前沒有可信的免費來源。缺就是缺，"
          "不要用記憶裡的數字填空，也不要寫成「約」「據估」——寫不出來就不寫。）",
          "- **現貨 ETF 每日淨流量**：唯一找到過的免費來源 SoSoValue 現在只回到 "
          "2025-06-06（過期 14 個月）",
          "- **ETH 質押率／驗證者數**：beaconcha.in 需要 API key",
          "- **長期持有者分佈、SOPR、已實現盈虧**：CoinMetrics community 未開放"]
    if sym == "ETH":
        L.append("- **發行與銷毀淨值（EIP-1559）**：沒有免費即時來源")
    return "\n".join(L)


if __name__ == "__main__":
    import sys
    _, md = fetch_crypto(sys.argv[1] if len(sys.argv) > 1 else "BTC")
    print(md)
