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
3. **沒有什麼**：ETF 淨流量與 ETH 質押率目前沒有可信免費來源（2026-08-19 實測，
   SoSoValue 只回到 2025-06-06、過期 14 個月；beaconcha.in 要 API key）。
   明寫「沒有」比留白安全——留白會被寫手用記憶裡的舊數字填滿。
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

    L += ["", "### 這份事實表**沒有**的東西（不可自行補寫）",
          "（2026-08-19 實測，這幾項目前沒有可信的免費來源。缺就是缺，"
          "不要用記憶裡的數字填空，也不要寫成「約」「據估」——寫不出來就不寫。）",
          "- **現貨 ETF 每日淨流量**：唯一找到過的免費來源 SoSoValue 現在只回到 "
          "2025-06-06（過期 14 個月）",
          "- **ETH 質押率／驗證者數**：beaconcha.in 需要 API key",
          "- **鏈上活躍地址、交易所餘額、長期持有者分佈**：沒有免費即時來源"]
    if sym == "ETH":
        L.append("- **發行與銷毀淨值（EIP-1559）**：沒有免費即時來源")
    return "\n".join(L)


if __name__ == "__main__":
    import sys
    _, md = fetch_crypto(sys.argv[1] if len(sys.argv) > 1 else "BTC")
    print(md)
