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
# 查證口吻（2026-11-20，主編拿範本對照後說文章「沒有溫度、很知識化」）：寫手在正文裡記帳——
# 「我查不到」「這是我的推論，不是事實」「這是 Notion 自己公佈的數字，沒有第三方核對過」「這只是影片的說法」。
# 11/17–11/19 三篇每千字 4–5 處（一篇十幾處），範本是 0。不擋稿；超過兩處就逐句請寫手刪掉或改成條件句。
_HEDGE = re.compile(
    r"查不到|找不到(?:可靠|獨立|原文|那份|公開|具體)|"
    r"(?:沒有|未經|尚未|還沒有?|未)(?:被)?(?:第三方|獨立(?:來源)?|外部(?:數據|資料)?)(?:可以)?(?:核對|核實|驗證|證實|佐證)|"
    r"(?:沒有|並未|未)(?:經過)?(?:第三方)?核對過?|"
    r"這(?:仍|也|只)?是我的(?:推論|推測|理解)|不是事實|推論只到這裡|"
    r"只能(?:把它)?當成[^，。]{0,8}(?:說法|陳述)|"
    r"(?:影片|節目|訪談|他|她|公司|官方|講者)(?:本人|自己)?的(?:說法|陳述)[，。]|"
    r"(?:訪談|節目|影片)(?:裡|中)?(?:沒有|並未)(?:說明|提供|給出|處理|直接這樣說|說|提)|"
    r"我不(?:把它當成|替他|替它)|先標一個限制|尚未(?:被)?(?:獨立)?(?:核實|證實)|"
    r"沒有(?:數據|資料)可以(?:核對|驗證)")
HEDGE_LIMIT = 2
# 一句塞四個以上數字：讀者讀完一個都記不住（Pfizer 那篇一段平均 7 個數字，範本一段平均 0.2 個）
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")

# 每句都冠節目名（「在 Moonshots 裡，」出現 9 次）：出處講一次就夠
_SHOW_PREFIX = re.compile(r"在\s?[〈《]?([A-Za-z][\w ’'&.\-]{1,30}|[\u4e00-\u9fff]{2,10})[〉》]?\s?(?:裡|中|節目裡|這集裡)，")

# 字面上也會用到的詞，只有當成轉場公式時才算套話（2026-11-16：「錢繞了一圈，又回到 NVIDIA 的營收裡」
# 是在解釋循環融資，被當成套話退了兩次）
_STOCK_PATTERNS = (
    (re.compile(r"繞了一圈[，,]?\s*(?:又|還是|最後|我們)?(?:又)?回到(?:了)?(?:原點|最初|一開始|開頭|開場|同一個問題|那個問題|這個問題)"),
     "繞了一圈回到原點"),
    # 2026-11-20 試寫：寫作契約的例句被照抄成固定收尾，四篇有三篇都是「我會先問兩件事」
    (re.compile(r"(?:我會)?先問(?:兩|三|二)件事"), "我會先問兩件事"),
    (re.compile(r"省下的錢落在誰|誰得先付(?:錢|出成本)改變習慣"), "照抄寫作契約的例句"),
    # 「一把尺」是寫作契約給寫手看的說法，試寫時被直接寫進文章（「這把尺成立的前提是…」）
    (re.compile(r"(?<![用拿])[這一那]把尺"), "把寫作契約的「一把尺」寫進文章"),
)

# 節目資料卡（「這集長 31 分鐘」「一支約 55 分鐘影片」）和先報架構（「以下我一段一段拆」）：讀者不需要
_SHOW_METADATA = re.compile(r"(?:這集|節目|影片|訪談|對談|這支)[^，。]{0,8}?(?:長|約|共)\s?約?\s?\d+\s?分鐘|"
                            r"\d+\s?分鐘(?:長)?的(?:影片|節目|訪談|對談|podcast|集)")
_SIGNPOST = re.compile(r"以下(?:我)?(?:一段一段|分幾段|分成|逐一|分[兩三四])|接下來(?:我們)?(?:來看|先看|一起看)|"
                       r"本文將|這篇(?:文章)?(?:會|要|將)(?:分|從.{0,6}談起)")

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


# 寫手沒拿到能寫的素材，就寫了一封「為什麼不寫」的說明信（2026-11-12〈7,307 字逐字稿沒送到我停筆〉，
# 553 字，只帶一般警告就進了草稿匣，差點被當備案發出去）。出現兩種以上就算，不能發。
_REFUSAL = (
    re.compile(r"寫不出來|寫不下去"), re.compile(r"沒有?(?:送到|傳到|交到)我(?:手上|這)?"),
    re.compile(r"沒有?(?:拿到|收到)(?:逐字稿|素材|全文|內容)"), re.compile(r"素材包"), re.compile(r"後設資料"),
    re.compile(r"我(?:選擇)?(?:不寫|停筆)|開天窗"), re.compile(r"(?:內容|逐字稿)(?:完全)?無法取得"),
)


def refusal_signals(article: str) -> list[str]:
    """回傳命中的說明信句型；兩種以上才算（單一個可能只是在談別人停筆）。"""
    hits = [m.group(0) for pat in _REFUSAL for m in [pat.search(article or "")] if m]
    return hits if len(hits) >= 2 else []


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
            "直接寫對的那個，不用交代原本哪裡錯；需要讓讀者知道是誰的說法，寫「他估計」「公司說」就夠。"))

    if re.search(r"^[#>*\s]*專有名詞(?:註解|解釋|小辭典)[*：:\s]*$", body, flags=re.M):
        items.append(_item(
            "文章中間插了一塊「專有名詞註解」清單，像課本",
            "專有名詞註解",
            "整塊拿掉。每個名詞在它第一次出現的那句話裡順口解釋，例如「GPU（訓練 AI 的主力晶片）」。"))

    sentences = [s.strip() for s in re.split(r"(?<=[。！？])|\n+", plain)
                 if s.strip() and not s.strip().startswith(("#", ">"))]
    hedges = [s for s in sentences if _HEDGE.search(s) and not _PROCESS_LEAK.search(s)]
    if len(hedges) > HEDGE_LIMIT:
        # 逐句列（小修只換被點名的句子）；前兩處先留著，通常是第一次交代出處的那句
        for s in hedges[HEDGE_LIMIT:HEDGE_LIMIT + 6]:
            items.append(_item(
                f"查證口吻太多：全文 {len(hedges)} 處在交代「查不到／沒核對／這是推論」（最多 {HEDGE_LIMIT} 處）",
                s,
                "讀者要的是你的判斷，不是查證紀錄。這句刪掉；或改成條件句，把不確定講成對世界的判斷"
                "（「如果這個數字屬實，……；如果偏高，……」），不要提查證過程。"))

    def _figures(s: str) -> set:      # 年份不算（「2026 年第二季營收年增 48%」只有一個數字要記）
        return {n for n in _NUMBER.findall(s) if not re.fullmatch(r"(?:19|20)\d\d", n)}

    for s in [s for s in sentences if len(_figures(s)) >= 4][:3]:
        items.append(_item(
            f"一句塞了 {len(_figures(s))} 個數字，讀者一個都記不住",
            s,
            "只留最能改變判斷的一兩個數字，後面接一句「這代表什麼」；其他數字刪掉（一整串的趨勢交給圖表）。"))

    for s in [s for s in sentences if _SHOW_METADATA.search(s) or _SIGNPOST.search(s)][:3]:
        meta = bool(_SHOW_METADATA.search(s))
        items.append(_item(
            "節目長度是資料卡，不是讀者要的資訊" if meta else "先報架構（「以下我一段一段拆」）：小標已經是架構了",
            s,
            "刪掉節目長度；節目名在開頭交代一次就夠。" if meta else "刪掉這句導覽，直接進內容。"))

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
