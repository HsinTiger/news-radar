"""文風檢查（2026-11-06，總編輯審稿）：程式抓得到的 AI 套話、沒翻譯的英文、重複段落、標籤式小標。

這些是「退稿意見」，不是擋稿條件：會跟審稿編輯的意見一起交給寫手重寫，但不影響
通過與否——一個用詞不該讓整天發不出稿。格式跟 quality_loop.length_feedback 一樣。
"""
from __future__ import annotations

import re

# 實際出現在 2026-11-03～06 四篇裡、每篇都套一次的句子（讀多了就是 AI 腔）
STOCK_PHRASES = (
    "全集最值得", "全集最精彩", "全集最扎實", "全集最有", "整集最值得", "這段我認為是",
    "我的判斷是", "最後留一個問題給你", "留一個問題給你", "接下來值得盯", "值得盯兩個訊號",
    "值得盯的訊號", "這裡出現了", "這個問題沒有標準答案", "繞了一圈", "值得注意的是",
    "總的來說", "綜上所述", "不難發現", "我看完這集的第一個念頭",
)

# 查證過程漏進正文（2026-11-16 黃仁勳那篇）：重寫的模型把審稿意見的話直接抄進文章，
# 讀者看到「我在提供的來源中沒有查到」「節目逐字稿寫成 ROSA，可能是轉錄誤差」「這裡不採用」。
# 讀者不知道什麼是「素材」「逐字稿」——那是我們的工作檯，不是文章。
_PROCESS_LEAK = re.compile(
    r"提供的(?:來源|材料|素材|資料|研究來源|逐字稿)|本次(?:提供|來源|素材)|(?:原始)?素材[裡中]|"
    r"逐字稿|轉錄誤差|研究來源|事實表|取材清單|(?:這裡|此處|本文)不採用|無法核對|以(?:後者|前者)為準|"
    r"以下採\S{0,12}的|沒有對應到")
# 每句都冠節目名（「在 Moonshots 裡，」出現 9 次）：出處講一次就夠
_SHOW_PREFIX = re.compile(r"在\s?[〈《]?([A-Za-z][\w ’'&.\-]{1,30}|[\u4e00-\u9fff]{2,10})[〉》]?\s?(?:裡|中|節目裡|這集裡)，")

_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)|https?://\S+|`[^`]*`")
_EN_RUN = re.compile(r"(?<![A-Za-z（(「“\"'])([a-z][a-z'\-]+(?:\s+[a-z][a-z'\-]+)+)(?![A-Za-z]*[）)」”\"'])")
# 英文小寫連用但其實是常見技術詞，不算沒翻譯
_EN_OK = {"open source", "machine learning", "deep learning", "fine tuning", "prompt engineering"}


def _item(problem: str, quote: str, fix: str) -> dict:
    return {"問題": problem, "原句": quote[:80], "建議改法": fix}


def style_feedback(article_md: str) -> list[dict]:
    text = article_md or ""
    body = re.split(r"^-{3,}\s*$", text, maxsplit=1, flags=re.M)[0]   # 文末取材區不檢查
    plain = _LINK.sub(" ", body)
    items: list[dict] = []

    hits = [p for p in STOCK_PHRASES if p in plain]
    if hits:
        quote = next((s for s in re.split(r"(?<=[。！？])", plain) if any(p in s for p in hits)), "")
        items.append(_item(
            f"AI 套話：{'、'.join(hits[:6])}。這些句子每篇都出現，讀者讀兩篇就認得出來",
            quote.strip(),
            "刪掉這類評語句，直接寫內容本身。要表達「這段最重要」，就把這段寫得最具體，不用宣告。"
            "結尾不要固定用「留一個問題給你」「接下來值得盯」，每篇換一種收法。"))

    leaks = list(dict.fromkeys(m.group(0) for m in _PROCESS_LEAK.finditer(plain)))
    if leaks:
        quote = next((x for x in re.split(r"(?<=[。！？])", plain) if _PROCESS_LEAK.search(x)), "")
        items.append(_item(
            f"查證過程寫進了文章：{'、'.join(leaks[:6])}。讀者不知道什麼是素材、逐字稿、研究來源",
            quote.strip(),
            "這些是寫稿時的工作檯，不是給讀者看的。查不到的說法整句刪掉；名稱或數字有出入的，"
            "直接寫對的那個，不用交代原本哪裡錯；真的要提醒還沒被證實，"
            "用一句人話（「這一點目前只有他自己這樣講」），一篇最多兩處。"))

    shows: dict[str, int] = {}
    for m in _SHOW_PREFIX.finditer(plain):
        shows[m.group(0)] = shows.get(m.group(0), 0) + 1
    worst = max(shows.items(), key=lambda kv: kv[1], default=("", 0))
    if worst[1] >= 4:
        items.append(_item(
            f"「{worst[0]}」出現 {worst[1]} 次，每句都冠節目名讀起來像筆錄",
            worst[0],
            "節目名在開頭交代一次，之後直接寫「某某說」「主持人問」，或用敘事帶過（「話題轉到…時」）。"))

    phrases = []
    for m in _EN_RUN.finditer(plain):
        ph = m.group(1).strip()
        if ph in _EN_OK or len(ph) < 8:
            continue
        line_start = plain.rfind("\n", 0, m.start()) + 1
        if plain[line_start:line_start + 1] in (">", "#"):
            continue
        phrases.append(ph)
    if phrases:
        uniq = list(dict.fromkeys(phrases))[:5]
        items.append(_item(
            f"英文沒翻譯：{'、'.join(uniq)}",
            uniq[0],
            "翻成自然的台灣中文；真的需要原文時，中文在前、英文放括號，而且只在第一次出現時附。"))

    paras = [p.strip() for p in re.split(r"\n\s*\n", plain) if len(p.strip()) > 40 and not p.startswith((">", "#"))]
    seen: dict[str, int] = {}
    repeats = []
    for i, p in enumerate(paras):
        flat = re.sub(r"\s+", "", p)
        for j in range(0, max(len(flat) - 18, 0), 6):
            g = flat[j:j + 18]
            if g in seen and seen[g] != i and not re.fullmatch(r"[\W\d]+", g):
                repeats.append(g)
                break
            seen.setdefault(g, i)
    if repeats:
        items.append(_item(
            "同一個論點在不同段落講了兩次",
            repeats[0],
            "保留講得比較完整的那一處，另一處刪掉或改寫成推進下一步的內容。"))

    heads = re.findall(r"^#{2,3}\s+(.+?)\s*$", body, flags=re.M)
    labels = [h for h in heads if len(re.sub(r"\s", "", h)) <= 6 and not h.endswith(("？", "?"))]
    if labels:
        items.append(_item(
            f"小標像標籤：{'、'.join(labels[:4])}",
            labels[0],
            "小標寫成一句主張或問題，讀者只掃小標也能知道這段的結論，例如「多 agent 多半只是角色扮演」。"))
    return items
