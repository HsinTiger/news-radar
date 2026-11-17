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
    "值得盯的訊號", "這裡出現了", "這個問題沒有標準答案", "值得注意的是",
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

# 字面上也會用到的詞，只有當成轉場公式時才算套話（2026-11-16：「錢繞了一圈，又回到 NVIDIA 的營收裡」
# 是在解釋循環融資，被當成套話退了兩次）
_STOCK_PATTERNS = (
    (re.compile(r"繞了一圈[，,]?\s*(?:又|還是|最後|我們)?(?:又)?回到(?:了)?(?:原點|最初|一開始|開頭|開場|同一個問題|那個問題|這個問題)"),
     "繞了一圈回到原點"),
)

_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)|https?://\S+|`[^`]*`")
_EN_RUN = re.compile(r"(?<![A-Za-z（(「“\"'])([a-z][a-z'\-]+(?:\s+[a-z][a-z'\-]+)+)(?![A-Za-z]*[）)」”\"'])")
# 英文小寫連用但其實是常見技術詞，不算沒翻譯
_EN_OK = {"open source", "machine learning", "deep learning", "fine tuning", "prompt engineering"}


# Hsin選觀點：素材只是引子，不能寫成讀後心得（主編 2026-11-13）。用「轉述句比例」量：
# 全文有幾成的句子是「他／來賓／節目／主要受訪者 說、認為、提到…」。2026-11-16 回頭量：
# 寫手看不到逐字稿的那幾篇 0–3%，看得到之後 11%、18%、23%、24%，一路變成重述來源。
_REPORT_VERB = r"(?:說|說到|提到|認為|表示|指出|舉例|舉了|主張|強調|回答|講|談到|承認|坦言|形容|建議|估計|解釋|回憶|觀察)"
_REPORT_ROLE = r"(?:他|她|他們|來賓|嘉賓|主持人|節目|影片|訪談|這集|對談|受訪者)"
_REPORT_NAME = r"(?:[A-Z][A-Za-z]+(?:\s[A-Z][A-Za-z]+)?|[\u4e00-\u9fff]{2,3}?)"
_REPORT_ADV = r"(?:也|還|則|又|曾|就|都|接著|後來)?"
_NOT_A_SPEAKER = re.compile(r"我們?|你們?|研究|報告|數據|資料|這個|這些|有人|作者|他們?|她|讀者|公司|政府|市場")
RETELLING_LIMIT = 0.15


def retelling_ratio(article_md: str) -> tuple[float, str]:
    """回 (轉述句比例, 主要說話者)。只看正文，不看文末取材。"""
    body = re.split(r"^-{3,}\s*$", article_md or "", maxsplit=1, flags=re.M)[0]
    body = re.sub(r"\]\([^)]*\)", "]", body)
    sents = [x.strip() for x in re.split(r"(?<=[。！？?!])|\n+", body)
             if len(x.strip()) >= 8 and not x.strip().startswith(("#", ">", "-", "*"))]
    if len(sents) < 10:
        return 0.0, ""
    speakers: dict[str, int] = {}
    for x in sents:
        for m in re.finditer(rf"({_REPORT_NAME}){_REPORT_ADV}\s?(?:在[^，。]{{0,10}})?{_REPORT_VERB}", x):
            name = m.group(1)
            # 「我傾向認為」「這也說明」不是受訪者在說話
            if not _NOT_A_SPEAKER.fullmatch(name) and not re.match(r"[我你這那有也都就還是會應可要能才並其另]", name):
                speakers[name] = speakers.get(name, 0) + 1
    main = max(speakers.items(), key=lambda kv: kv[1], default=("", 0))
    who = main[0] if main[1] >= 3 else ""
    names = f"{_REPORT_ROLE}|{re.escape(who)}" if who else _REPORT_ROLE
    pat = re.compile(rf"(?:{names}){_REPORT_ADV}\s?(?:在[^，。]{{0,10}})?{_REPORT_VERB}")
    return sum(1 for x in sents if pat.search(x)) / len(sents), who


def retelling_feedback(article_md: str) -> list[dict]:
    ratio, who = retelling_ratio(article_md)
    if ratio <= RETELLING_LIMIT:
        return []
    return [_item(
        f"還在轉述原素材：全文 {ratio:.0%} 的句子是「{who or '他'}說／認為／提到…」（上限 {RETELLING_LIMIT:.0%}）",
        f"{who or '他'}說",
        "原素材只在開頭當引子交代一次。把主線換成那個問題本身：用取材裡的延伸資料、別的產業或年代的前例、"
        "反方說法一層一層問下去；受訪者的話全文留五六句最關鍵的就好。小標改成你要問的問題，不是他的論點。")]


def _item(problem: str, quote: str, fix: str) -> dict:
    return {"問題": problem, "原句": quote[:80], "建議改法": fix}


def style_feedback(article_md: str) -> list[dict]:
    text = article_md or ""
    body = re.split(r"^-{3,}\s*$", text, maxsplit=1, flags=re.M)[0]   # 文末取材區不檢查
    plain = _LINK.sub(" ", body)
    items: list[dict] = []

    hits = [p for p in STOCK_PHRASES if p in plain] + [name for pat, name in _STOCK_PATTERNS if pat.search(plain)]
    if hits:
        quote = next((s for s in re.split(r"(?<=[。！？])", plain)
                      if any(p in s for p in hits) or any(pat.search(s) for pat, _ in _STOCK_PATTERNS)), "")
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

    if re.search(r"^[#>*\s]*專有名詞(?:註解|解釋|小辭典)[*：:\s]*$", body, flags=re.M):
        items.append(_item(
            "文章中間插了一塊「專有名詞註解」清單，像課本",
            "專有名詞註解",
            "整塊拿掉。每個名詞在它第一次出現的那句話裡順口解釋，例如「GPU（訓練 AI 的主力晶片）」。"))

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
