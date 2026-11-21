"""來源分級（2026-11-21 週末旗艦改版）。

A：一手文件，例如 SEC 申報、公司 IR、新聞稿、政府與交易所、論文、法院文件。
B：一線媒體。
C：其他分析、專家、產業網站（預設）。
D：數字整理站：只能當線索，不能當數字出處。
   2026-11-21 蘋果那篇有 4 個來源是這類，出錯的歐洲營收數字也從這裡來。
"""
from __future__ import annotations

from urllib.parse import urlparse

_A = ("sec.gov", "investor.", "ir.", "investors.", "mops.twse.com.tw", "twse.com.tw", "tpex.org.tw",
      ".gov", "gov.tw", "arxiv.org", "nber.org", "courtlistener.com", "uscourts.gov",
      "apple.com/newsroom", "/newsroom", "/press-release", "/press/", "businesswire.com", "prnewswire.com",
      "globenewswire.com")
_B = ("reuters.com", "bloomberg.com", "ft.com", "wsj.com", "nytimes.com", "cnbc.com", "theinformation.com",
      "economist.com", "asia.nikkei.com", "nikkei.com", "theverge.com", "techcrunch.com", "semianalysis.com",
      "stratechery.com", "cw.com.tw", "businessweekly.com.tw", "bnext.com.tw", "technews.tw", "udn.com",
      "ctee.com.tw", "digitimes.com", "cna.com.tw", "axios.com", "barrons.com", "marketwatch.com")
_D = ("axis-intelligence.com", "markhub24.com", "companiesmarketcap.com", "tradingview.com", "businessquant.com",
      "macrotrends.net", "stockanalysis.com", "statista.com", "demandsage.com", "backlinko.com",
      "bankmycell.com", "businessofapps.com", "gurufocus.com", "wallstreetzen.com", "simplywall.st",
      "finbox.com", "zacks.com", "fool.com", "seekingalpha.com", "investing.com", "marketbeat.com")

LABEL = {"A": "A 級一手文件", "B": "B 級一線媒體", "C": "C 級分析", "D": "D 級整理站（只能當線索）"}


def tier(url: str) -> str:
    u = (url or "").lower()
    host = urlparse(u).netloc.removeprefix("www.")
    path_host = host + urlparse(u).path
    if any(h in host for h in _D):
        return "D"
    if any(a in path_host if "/" in a else (host.startswith(a) if a.endswith(".") else a in host) for a in _A):
        return "A"
    if any(b in host for b in _B):
        return "B"
    return "C"
