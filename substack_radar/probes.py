"""隱藏抽查（2026-11-21 週末旗艦第 1 步）。

這裡的規則不寫進寫手或調研員的指令，它們不知道哪些地方會被查：
1. truth_probe：稿子裡的年度營收、毛利、營業利益、淨利，跟 SEC 結構化資料比對。
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
    "營收": r"(總)?營收|營業收入|銷售額",
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
_SEGMENT = re.compile(r"地區|分部|部門|大中華|中國|歐洲|美洲|日本|亞太|服務|iPhone|Mac|iPad|產品線|業務|事業")


def _sentences(article: str) -> list[str]:
    body = article.split("### 📚 本文取材")[0]
    return [s for s in re.split(r"(?<=[。！？\n])", body) if s.strip()]


def truth_probe(article: str, truth: dict, tolerance: float = 0.015) -> list[str]:
    out = []
    for sent in _sentences(article):
        if not _ANNUAL.search(sent) or _QUARTER.search(sent):
            continue
        years = {int(y) for y in _YEAR.findall(sent)}
        for label, pat in _LABELS.items():
            rows = truth.get(label) or []
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
