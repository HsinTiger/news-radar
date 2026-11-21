"""隱藏抽查（2026-11-21 週末旗艦第 1 步）。

這裡的規則不寫進寫手或調研員的指令，它們不知道哪些地方會被查：
1. truth_probe：稿子裡的年度營收、毛利、營業利益、淨利，跟 SEC 結構化資料比對。
   台股（證交所／櫃買中心）的累計損益用台幣另外比對：truth 的列有 "twd" 才查，只認「X 億元」「X 億新台幣」。
2. citation_probe：隨機抽幾句「帶連結又帶數字」的句子，重抓那個網頁，看數字是不是真的在上面。
   抓的是「張冠李戴」：數字是真的，卻掛在沒寫這個數字的來源底下。
   2026-11-21 蘋果那篇的「49% 毛利率」掛在一篇只講到 FY2024 的文章底下，就是這種。
抽查沒過就標「[跟素材不符，還沒修]」，發稿前交給糾錯台處理。
"""
from __future__ import annotations

import random
import re
from datetime import date

from substack_radar.fix_desk import FLAG, _number_in, page_text

_LABELS = {
    "營收": r"(?:(總)?營收|營業收入|銷售額)(?!成本|占|佔|比重|比例)",   # 「營收成本」是成本，不是營收（MSFT 預演）
    "毛利": r"毛利(?!率)",
    "營業利益": r"營業利益|營益(?!率)|營業利潤",
    "淨利": r"淨利(?!率)|稅後淨利|淨收入",
}
_ANNUAL = re.compile(r"全年|年度|整年|FY\s?\d{4}|會計年度|財年")
_QUARTER = re.compile(r"季|上半年|下半年|月")
_YEAR = re.compile(r"(?:FY\s?|會計年度\s?|財年\s?)?(20\d{2})\s*(?:年|財年|會計年度)?")
_AMOUNT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(億|兆)\s*美元")
_LINKED = re.compile(r"\[[^\]]+\]\((https?://[^)\s]+)\)")
_NEAR_AMOUNT = re.compile(r"[^，。；、,;\d]{0,10}?(\d[\d,]*(?:\.\d+)?)\s*(億|兆)\s*美元")
# 台幣：稿子寫「X 億元」「X 億新台幣」才算（億美元不算）
_TWD_AMOUNT = re.compile(r"[^，。；、,;\d]{0,10}?(\d[\d,]*(?:\.\d+)?)\s*(億|兆)\s*(?:元|新台幣|新臺幣)")
_TWD_END = re.compile(r"(\d{4})Q([1-4])")
_RNG = r"\s*[–—~～至到\-]\s*"
# 累計到第幾季的各種寫法；對不上的句子不判
_TWD_PERIOD = {
    1: re.compile(r"第一季|第\s*1\s*季|首季|Q1|1" + _RNG + r"3\s*月|前三個月|前\s*3\s*個月"),
    2: re.compile(r"上半年|前兩季|前二季|前\s*2\s*季|第二季累計|第\s*2\s*季累計|1" + _RNG + r"6\s*月|前六個月|前\s*6\s*個月"),
    3: re.compile(r"前三季|前\s*3\s*季|第三季累計|第\s*3\s*季累計|1" + _RNG + r"9\s*月|前九個月|前\s*9\s*個月"),
    4: re.compile(r"全年|整年|前四季|前\s*4\s*季|第四季累計|第\s*4\s*季累計|1" + _RNG + r"12\s*月|前十二個月|前\s*12\s*個月"),
}
_SINGLE_Q = re.compile(r"第\s*[一二三四1-4]\s*季(?!\s*累計)|單季|當季|Q[1-4](?!\s*累計)")
_SEGMENT = re.compile(r"地區|分部|部門|大中華|中國|歐洲|美洲|日本|亞太|服務|iPhone|Mac|iPad|產品線|業務|事業|"
                      r"雲端|Azure|Office|Windows|LinkedIn|Xbox|遊戲|廣告|硬體|軟體|授權|訂閱|資料中心|晶片|AI")


def _sentences(article: str) -> list[str]:
    body = article.split("### 📚 本文取材")[0]
    return [s for s in re.split(r"(?<=[。！？\n])", body) if s.strip()]


def _twd_probe(sent: str, truth: dict, tolerance: float) -> list[str]:
    """台幣累計數字抽查：要同一句同時有同年份和對得上的累計期間（前三季、1–9 月……）才判。"""
    out = []
    for clause in re.split(r"[；;]", sent):
        years = {int(y) for y in _YEAR.findall(re.sub(r"\d[\d,]*(?:\.\d+)?\s*(?:億|兆|萬|%)", "", clause))}
        if len(years) != 1:
            continue                                      # 沒寫年份或一句講好幾年：不判
        for label, pat in _LABELS.items():
            m_label = re.search(pat, clause)
            if not m_label:
                continue
            if _SEGMENT.search(clause[:m_label.start()][-20:]):
                continue
            m = _TWD_AMOUNT.match(clause[m_label.end():])
            if not m:
                continue
            value = float(m.group(1).replace(",", "")) * (1e8 if m.group(2) == "億" else 1e12)
            for row in truth.get(label) or []:
                if row.get("twd") is None:
                    continue
                e = _TWD_END.search(str(row.get("end", "")))
                if not e or int(e.group(1)) not in years:
                    continue
                q = int(e.group(2))
                if not _TWD_PERIOD[q].search(clause) or (q != 1 and _SINGLE_Q.search(clause)):
                    continue                              # 期間對不上（或混了單季數字）：不判
                twd = row["twd"]
                if abs(value - twd) > twd * tolerance:
                    out.append(f"{FLAG} 隱藏抽查：{label} {row['end']} "
                               f"稿子寫 {m.group(1)} {m.group(2)}元，證交所／櫃買中心公開資料是 {twd / 1e8:,.1f} 億元"
                               f"（原句：{sent.strip()[:60]}）")
                break
    return out


def truth_probe(article: str, truth: dict, tolerance: float = 0.015) -> list[str]:
    out = []
    for sent in _sentences(article):
        out.extend(_twd_probe(sent, truth, tolerance))
        if not _ANNUAL.search(sent) or _QUARTER.search(sent):
            continue
        years = {int(y) for y in _YEAR.findall(sent)}
        for label, pat in _LABELS.items():
            rows = [r for r in (truth.get(label) or []) if r.get("usd") is not None]   # 只比美元列
            m_label = re.search(pat, sent)
            if not rows or not m_label:
                continue
            if _SEGMENT.search(sent[:m_label.start()][-20:]):
                continue                                  # 「歐洲營收」「服務營收」是分部，不是全公司
            m = _NEAR_AMOUNT.match(sent[m_label.end():])  # 科目後面緊接的金額才算，中間不能隔標點
            if not m:
                continue
            value = float(m.group(1).replace(",", "")) * (1e8 if m.group(2) == "億" else 1e12)
            cands = [r for r in rows if r["fy"] in years] if years else rows[:1]
            if len(years) > 1 or not cands:
                continue                                  # 一句講好幾年，對不上哪年就不判
            truth_v = cands[0]["usd"]
            if abs(value - truth_v) > truth_v * tolerance:
                out.append(f"{FLAG} 隱藏抽查：{label} FY{cands[0]['fy']} 稿子寫 {m.group(1)} {m.group(2)}美元，"
                           f"SEC 年報是 {truth_v / 1e8:,.1f} 億美元（原句：{sent.strip()[:60]}）")
    return out


def citation_probe(article: str, *, k: int = 4, fetch=None, seed: str | None = None) -> list[str]:
    """隨機抽 k 句「帶連結＋帶金額或百分比」的句子，重抓連結頁面，數字找不到就標記。
    抽樣種子用日期：同一天重跑結果一樣，寫手事先猜不到會抽哪句。"""
    from substack_radar.source_tier import tier

    pool = []
    for sent in _sentences(article):
        # 連結只替它所在的那個分句作證（「；」隔開的前半句不算）
        for clause in re.split(r"[；;]", sent):
            links = _LINKED.findall(clause)
            nums = [n for n, _u in re.findall(r"(\d[\d,]*(?:\.\d+)?)\s*(億|兆|%)", _LINKED.sub("", clause))
                    + re.findall(r"(\d[\d,]*(?:\.\d+)?)\s*(億|兆|%)", " ".join(
                        m.split("](")[0] for m in re.findall(r"\[[^\]]+\]\(", clause)))]
            if len(links) == 1 and nums:
                pool.append((clause, links[0], [n.replace(",", "") for n in nums]))
    if not pool:
        return []
    rng = random.Random(seed or date.today().isoformat())
    # D 級整理站優先抽：最常出事
    pool.sort(key=lambda x: 0 if tier(x[1]) == "D" else 1)
    picked = pool[:1] + rng.sample(pool[1:], min(k - 1, len(pool) - 1)) if pool else []
    if fetch is None:
        import httpx

        def fetch(u):
            r = httpx.get(u, timeout=25, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 news-radar-probe"})
            r.raise_for_status()
            return r.text
    out = []
    for sent, url, nums in picked:
        try:
            text = re.sub(r"(?<=\d),(?=\d{3})", "", page_text(fetch(url)))
        except Exception:
            continue                                      # 抓不到不算證據不足，也不算錯
        missing = [n for n in nums if not _number_in(n, text)]
        if missing:
            clean = _LINKED.sub(lambda m: m.group(0).split("](")[0].lstrip("["), sent).strip()
            out.append(f"{FLAG} 隱藏抽查：這句引用的網頁上找不到 {', '.join(missing)}（{url}）（原句：{clean[:60]}）")
    return out
