"""AEO 閘門：讓文章被 AI 引用得到。

來源：2026-08-19 萃取的兩支影片（Debug 土撥鼠《AEO》《SEO 生存指南》）。
Substack 是託管平台，robots.txt／SSR／sitemap／JSON-LD／301 我們都碰不到，
所以只實作「寫作端能控制、而且程式驗得出來」的那幾條。

實作的三條與各自的依據
----------------------
A1 **答案先行**：標題底下先用 2–4 句直接給結論，再展開條件與證據 [07:58-08:05]。
   AI 是從網頁擷取單一片段去跟別的來源比較，鋪陳完才給答案的文章擷取不到重點。

A2 **段落要能被單獨讀懂**：明確交代主詞、日期、條件、依據 [07:38-07:57]。
   代名詞開頭的句子（「它的效果很好，比前面的方法快」）被切出來之後，
   AI 認不出主詞，就不會引用。

A3 **重要數據標日期**[09:50-10:34]。AI 在「模糊的真相」與「具體的虛構」之間
   會選具體的虛構；官方不給精確且標日期的資訊，它就自己拼湊。
   研究：被 AI 引用的內容平均比傳統搜尋結果新鮮 25.7% [11:33-11:36]。

刻意沒實作的
------------
字數。研究顯示字數與被引用的相關性只有 0.04、超過一半被引用頁面不到 1,000 字
[08:40-08:48]，而我們現在是 2,275–3,500 字（實測中位數 2,929）。這是編輯定位
問題不是程式問題——縮到 1,000 字等於換一種產品，要 owner 決定，不該由閘門偷偷改。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 以代名詞開頭＝這段被單獨切出來時認不出在講誰
_ORPHAN_OPENERS = ("它", "他們", "這個", "這些", "那個", "那些", "此舉", "該公司",
                   "該項", "上述", "前者", "後者", "這一點", "這種", "這樣")
_SENTENCE_END = re.compile(r"[。！？]")
# 有時效性、卻常被寫成永久事實的量
_DATED_METRICS = ("市值", "股價", "本益比", "殖利率", "毛利率", "營益率", "淨利率",
                  "市佔", "市占", "算力", "流通量")
# 明確時點：要有年份或「截至／撰稿時」這種錨定語，光有「目前」不算。
_ANCHOR = re.compile(r"(截至|撰稿時|本文寫作時)|20\d{2}\s*年\s*\d{1,2}\s*月")


@dataclass(frozen=True)
class AeoIssue:
    rule: str
    detail: str
    sample: str

    def __str__(self) -> str:
        return f"[{self.rule}] {self.detail}\n        例：{self.sample[:64]}"


def _sections(article_md: str) -> list[tuple[str, str]]:
    """回傳 (小標, 該節第一段)。沒有小標的整篇當一節。"""
    parts = re.split(r"^#{2,4}\s+(.+?)$", article_md or "", flags=re.M)
    if len(parts) < 3:
        first = next((p.strip() for p in re.split(r"\n\s*\n", article_md or "") if p.strip()), "")
        return [("(全文)", first)]
    out = []
    for i in range(1, len(parts) - 1, 2):
        head, body = parts[i].strip(), parts[i + 1]
        para = next((p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()), "")
        out.append((head, para))
    return out


def check(article_md: str, *, lead_max_sentences: int = 4) -> list[AeoIssue]:
    issues: list[AeoIssue] = []
    text = article_md or ""

    # A1 答案先行：主標之後、第一個小標之前的開場，句數不該超過 lead_max_sentences
    body_after_title = re.sub(r"^#\s+.+?$", "", text, count=1, flags=re.M)
    body_after_title = re.sub(r"^>.*$", "", body_after_title, flags=re.M)   # 產文路線引用區塊
    body_after_title = re.sub(r"^\*.+?\*$", "", body_after_title, flags=re.M)  # 副標
    lead = re.split(r"^#{2,4}\s+", body_after_title, maxsplit=1, flags=re.M)[0]
    lead_sentences = [s for s in _SENTENCE_END.split(lead) if len(s.strip()) > 8]
    if len(lead_sentences) > lead_max_sentences + 2:
        issues.append(AeoIssue(
            rule="A1 開場沒有答案先行",
            detail=f"第一個小標之前有 {len(lead_sentences)} 句才進入正文；"
                   f"AI 擷取的是單一片段，鋪陳太長會擷取不到結論。"
                   f"請在開頭 2–{lead_max_sentences} 句內直接給出本文的判斷。",
            sample=lead.strip()[:70],
        ))

    # A2 段落要能被單獨讀懂：小標後第一句不要以代名詞開頭
    orphans = [(head, para) for head, para in _sections(text)
               if para.strip().startswith(_ORPHAN_OPENERS)]
    for head, para in orphans[:3]:
        issues.append(AeoIssue(
            rule="A2 段落無法被單獨讀懂",
            detail=f"小標「{head[:20]}」下的第一句以代名詞開頭。"
                   "這段被 AI 單獨切出來時認不出主詞，就不會被引用。改成明確主詞。",
            sample=para.strip()[:70],
        ))

    # A3 時效性數據要有時間錨點。
    # 第一版逐句檢查有沒有日期，誤報很兇——時間錨點本來就只在開頭講一次，
    # 後面每句再標一次反而囉嗦。改成看「全篇有沒有」：有會過期的指標與數字，
    # 卻通篇找不到一個明確時點，才算違規。
    has_dated_metric = any(
        any(m in sentence for m in _DATED_METRICS) and re.search(r"\d", sentence)
        for sentence in _SENTENCE_END.split(text)
    )
    if has_dated_metric and not _ANCHOR.search(text):
        issues.append(AeoIssue(
            rule="A3 全篇沒有時間錨點",
            detail="文中有會過期的指標與數字（市值／股價／本益比／毛利率／市佔…），"
                   "卻通篇沒有一句「截至 X 年 X 月」。AI 偏好新鮮且可驗證的內容；"
                   "沒有時點的數字兩個月後就是錯的，而讀者無從判斷。"
                   "在第一次出現關鍵數字的地方補一個明確時點就夠。",
            sample=next((s.strip() for s in _SENTENCE_END.split(text)
                         if any(m in s for m in _DATED_METRICS) and re.search(r"\d", s)), "")[:70],
        ))
    return issues
