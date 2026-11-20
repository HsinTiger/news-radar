"""標題台與 30 秒重點（2026-11-06，信哥要求以總編輯角度提升首次點閱率與每日黏著度）。

稽核迴圈結束、正文定稿之後才跑：
1. 標題：模型依定稿正文寫 5 個候選，自評一個最好的；程式再擋掉超過 15 字、有冒號、
   切斷英文單字的候選。全部不過就保留寫手原本的標題。
2. 30 秒看重點：三條，給只滑手機的讀者也有收穫；每條裡的數字必須在正文出現過，
   沒對上的那條丟掉，剩不到兩條就不放。

模型掛了一律保留原樣，不能因為包裝環節失敗就掉稿。
"""
from __future__ import annotations

import json
import re

TITLE_MAX = 15

_PROMPT = """你是一份中文電子報的資深總編輯，替下面這篇已定稿的文章做兩件事。
讀者是台灣的知識工作者、工程師、投資人，在手機收件匣只看得到標題前 15 個字。

專欄：{column}
寫手原本的標題：{title}
副標：{subtitle}

【一】寫 6 個標題候選，每個 ≤15 個字：
- 前 3 個「有張力」：帶一個文章確實支持的張力字（例如卡住、虧、欠、輸、代價、危機、來不及），
  講出誰會損失什麼、哪裡出了問題。
- 後 3 個「中性」：不用負面字，靠一個具體數字、一個違反直覺的反差，或讀者的處境讓人想點開。
- 兩組都要讓讀者對號入座：寫他的處境、他的錢、他的工作會怎樣。
- 用常見的字、短句，不用抽象詞（悖論、範式、本質、維度、賦能、敘事、機制……）：分析三萬多組標題實驗的研究發現，
  用字越簡單，點的人越多。
- 不要冒號、不要「｜」、不要節目名、不要問「你知道嗎」這類空問句；英文詞一律完整，不要截斷。
- 只能用文章裡有的事實，不誇大、不恐嚇；不要「震驚」「必看」「竟然」「99% 的人」這類標題黨字眼。
- 標題要講這篇的主線（寫手原本的標題在講的那件事），不能拿文中一個支線案例或細節當標題。
兩組各自把你認為點閱率最高的放在該組第一個；best 填全部 6 個裡你最看好的序號（1–6）。

【二】寫「30 秒看重點」三條，每條 ≤40 字：
- 只滑到這裡就離開的讀者，也能帶走這篇最重要的三件事。
- 寫結論本身（誰、做了什麼、代表什麼），不寫「本文將…」，也不寫「尚未核實」「只是他的說法」這種查證紀錄。數字必須跟文章完全一致。

【三】寫一則 Substack Notes（站內動態，給還沒訂閱的人看），120–220 字：
- 像作者本人隨手發的一段話，不是廣告：先丟出文章裡最讓人意外的一個事實或一個問題，
  再用一兩句說為什麼值得想，最後一句留懸念。不要「歡迎訂閱」「點擊閱讀」，不要 hashtag、不要網址。
- 有判斷就說「我目前傾向……」，不要「我的判斷是：」這種標籤；也不要用「什麼條件下我會改這個判斷？」這類固定反問收尾。
- 只能用文章裡有的事實與數字。

【四】重寫小標。這篇目前的小標依序是：
{subheads}
把它們抄下來連著讀，要像一條論證：主張 → 讀者心裡的「可是」→ 所以呢。每個小標用平實的一句話說出那一節的結論，
或那一節要回答的問題（大約三分之一是問句）。像報告章節的（「某某這條線」「綜合起來看」「最強反方」「我的判斷」
「查不到的部分」「接下來盯哪些訊號」）就改掉。
- 數量與順序不變（第 n 個對第 n 段），每個 8–20 個字，不要冒號。
- 不要每個都用「X，Y」同一種句型；不要用「那」「所以」開頭硬接上一個，也不要太口語（「扯得上嗎」）。
- 只能用該段已有的事實，不新增數字。原本就清楚的保留原樣。
- 文章沒有小標就回空陣列。

只輸出 JSON：{{"titles": ["", "", "", "", "", ""], "best": 1, "key_points": ["", "", ""], "note": "", "subheads": []}}

【文章】
{body}"""


def title_ok(title: str) -> bool:
    t = (title or "").strip()
    # 字數不算空格：「上半年營收 163 億」的空格是排版，手機收件匣也不會因此多佔一個字
    return bool(t) and len(re.sub(r"\s", "", t)) <= TITLE_MAX and not re.search(r"[：:｜|]", t)


def _to_traditional(text: str) -> str:
    """閘道模型有時吐簡體（2026-11-06 試跑：「营收」「亿」）。用 s2tw 只換字形，
    不用 s2twp——那個會改詞（項目→專案，2026-10 吃過虧）。"""
    try:
        import opencc
        text = text or ""
        # 只有真的是簡體才轉：對繁體跑 s2tw 會把「只買」改成「隻買」、「簽」改成「籤」
        # （2026-11-11 那篇的 30 秒重點就這樣被改壞）。判斷方式：繁轉簡後幾乎沒變＝本來就是簡體。
        simplified = opencc.OpenCC("t2s").convert(text)
        changed = sum(a != b for a, b in zip(text, simplified))
        if changed > max(1, len(text) * 0.03):
            return text
        return opencc.OpenCC("s2tw").convert(text)
    except Exception:
        return text or ""


def pangu(text: str) -> str:
    """簡轉繁，中英文之間加空格（「AI代理」→「AI 代理」），跟正文寫法一致。"""
    text = _to_traditional(text)
    text = re.sub(r"([\u4e00-\u9fff])([A-Za-z0-9%$])", r"\1 \2", text or "")
    return re.sub(r"([A-Za-z0-9%])([\u4e00-\u9fff])", r"\1 \2", text)


def _numbers(text: str) -> set:
    # 千分位逗號不算差異：「2650 億」和正文的「2,650 億」是同一個數字
    return set(re.findall(r"\d+(?:\.\d+)?", re.sub(r"(?<=\d),(?=\d{3})", "", text or "")))


def grounded_points(points: list, body: str) -> list:
    body_nums = _numbers(body)
    out = []
    for p in points or []:
        p = re.sub(r"^\s*(?:[-•・*]|\d{1,2}[.、)])\s+", "", str(p)).strip()
        if not p or len(p) > 60:
            continue
        if not _numbers(p) <= body_nums:
            print(f"[HeadlineDesk] ⏭️ 重點裡的數字正文沒有，丟掉：{p[:40]}")
            continue
        from substack_radar.style_gate import _HEDGE, _PROCESS_LEAK
        if _HEDGE.search(p) or _PROCESS_LEAK.search(p):
            # 「影片視為轉折點，但尚未獨立核實」：只看重點的讀者帶走的是一句查證紀錄（2026-11-19）
            print(f"[HeadlineDesk] ⏭️ 重點寫成查證紀錄，丟掉：{p[:40]}")
            continue
        out.append(pangu(p))
    return out[:3]


_SUBHEAD = re.compile(r"^(#{2,3})[ \t]+(.+?)[ \t]*$", re.M)
# 2026-11-20：加上框架模板留下的章節名（「最強反方怎麼說」「研究包查不到的部分」「接下來盯哪三個訊號」）
_REPORT_WORDS = re.compile(r"這條線|綜合(?:起來|來看)|其他線索|小結|總結|最強反方|我的判斷|願意認錯|查不到|"
                           r"研究包|接下來(?:要)?盯|觀測指標|可能錯在哪")


def _terms(text: str) -> set:
    t = re.sub(r"\s", "", (text or "").lower())
    latin = set(re.findall(r"[a-z][a-z0-9]{1,}", t))
    cjk = {t[i:i + 2] for i in range(len(t) - 1) if re.fullmatch(r"[\u4e00-\u9fff]{2}", t[i:i + 2])}
    return latin | cjk


def on_topic(candidate: str, anchor: str) -> bool:
    """新標題至少要跟寫手的標題或副標共用一個詞（2026-11-17：一篇講 AI 自己改工具的文章，
    標題被換成文中支線案例「迪士尼撤回 10 億美元投資」）。寫手沒給標題時不擋。"""
    a = _terms(anchor)
    return not a or bool(_terms(candidate) & a)


def subhead_ok(text: str, body: str) -> bool:
    t = (text or "").strip()
    n = len(re.sub(r"\s", "", t))
    return (5 <= n <= 24 and not re.search(r"[：:｜|#\n]", t) and not _REPORT_WORDS.search(t)
            and not re.match(r"(?:那|所以)", t)        # 「那設局說到底多薄」：硬接上一個小標
            and _numbers(t) <= _numbers(body))


def apply_subheads(body: str, new_heads: list) -> tuple[str, int]:
    """把模型改寫的小標換進正文。數量對不上就整批不換；單一個不合格就保留那一個原樣。
    原本就合格的小標也不換（2026-11-20 試跑：「他說中國在表演，卻把中國當頭號對手」被改成
    「他前後的說法並不一致」，把主編剛改掉的誤讀又寫回去，還跟內文矛盾）。只換有毛病的：報告章節名、冒號、
    「那／所以」開頭、太長太短。回 (新正文, 換掉幾個)。只動 ## / ### 那一行，段落內容一個字都不碰。"""
    heads = _SUBHEAD.findall(body or "")
    if not heads or not isinstance(new_heads, list) or len(new_heads) != len(heads):
        return body, 0
    it = iter(new_heads)
    changed = 0

    def repl(m):
        nonlocal changed
        new = pangu(str(next(it) or "").strip().lstrip("#").strip())
        if new and new != m.group(2) and not subhead_ok(m.group(2), body) and subhead_ok(new, body):
            changed += 1
            return f"{m.group(1)} {new}"
        return m.group(0)

    return _SUBHEAD.sub(repl, body), changed


def polish(*, title: str, subtitle: str, body: str, column: str, chat=None, arm: str | None = None) -> dict:
    """回 {"title": 新標題或 None, "key_points": [..], "title_arm": .., "title_style": ..}。chat(prompt)->str 可注入測試。

    標題實驗（2026-11-20，主編：張力要多少交給實驗數據決定）：每篇由 reader_signals.title_arm 抽「有張力／中性」
    其中一組，從那組的候選裡挑；那組沒有合格的才用另一組。title_style 記實際用上的那一組，每週報表拿去比讀者數。"""
    if chat is None:
        # 包裝是寫作的活，跟寫稿同一個順序（Haiku 優先）。原本固定用閘道稽核模型，
        # 2026-11-16 實測一次要 617 秒；Haiku 不到一分鐘。
        from substack_radar.quality_loop import run_writer_text

        def chat(prompt):
            return run_writer_text(prompt, 600)[0]
    try:
        from src.llm_brain import _extract_json_blob

        heads = [h for _, h in _SUBHEAD.findall(body or "")]
        listed = "\n".join(f"{i}. {h}" for i, h in enumerate(heads, 1)) or "（沒有小標）"
        raw = chat(_PROMPT.format(column=column or "—", title=title, subtitle=subtitle or "",
                                  subheads=listed, body=(body or "")[:14000]))
        data = json.loads(_extract_json_blob(raw) or "{}")
    except Exception as exc:
        print(f"[HeadlineDesk] ⚠️ 標題台失敗，保留原標題、不放重點：{str(exc)[:100]}")
        return {"title": None, "key_points": [], "note": "", "body": body, "subheads_changed": 0,
                "title_arm": None, "title_style": None}

    titles = [pangu(str(t).strip()) for t in data.get("titles") or [] if str(t).strip()]
    try:
        best = int(data.get("best") or 1)
    except (TypeError, ValueError):
        best = 1
    from substack_radar.reader_signals import ARM_LABEL, is_clickbait, title_arm, title_complexity, title_style

    arm = arm or title_arm(seed=f"{column}|{title}")
    ordered = ([titles[best - 1]] if 1 <= best <= len(titles) else []) + titles
    anchor = f"{title} {subtitle}"
    usable = [t for t in ordered if title_ok(t) and on_topic(t, anchor) and not is_clickbait(t)]
    # 同一組裡挑用字最簡單的；一樣簡單就照總編輯的排序（sorted 是穩定排序）
    in_arm = sorted((t for t in usable if title_style(t) == arm), key=title_complexity)
    chosen = next(iter(in_arm), None) or next(iter(sorted(usable, key=title_complexity)), None)
    for t in titles:
        mark = "✅" if t == chosen else ("❌" if not title_ok(t) or is_clickbait(t) else "  ")
        print(f"[HeadlineDesk] 候選{mark}［{ARM_LABEL[title_style(t)]}］{t}")
    print(f"[HeadlineDesk] 標題實驗：這篇抽到「{ARM_LABEL[arm]}」組"
          + ("" if not chosen or title_style(chosen) == arm else f"，但那組沒有合格的，改用「{ARM_LABEL[title_style(chosen)]}」"))
    points = grounded_points(data.get("key_points") or [], body)
    if len(points) < 2:
        points = []
    note = pangu(str(data.get("note") or "").strip())
    if not (60 <= len(note) <= 400) or not _numbers(note) <= _numbers(body) or re.search(r"https?://|#\S", note):
        if note:
            print("[HeadlineDesk] ⏭️ Notes 草稿不合格（長度、數字或含網址），這篇不附")
        note = ""
    new_body, n_heads = apply_subheads(body, data.get("subheads") or [])
    if n_heads:
        for (_, a), (_, b) in zip(_SUBHEAD.findall(body), _SUBHEAD.findall(new_body)):
            if a != b:
                print(f"[HeadlineDesk] 小標：{a} → {b}")
    return {"title": chosen, "key_points": points, "note": note, "body": new_body, "subheads_changed": n_heads,
            "title_arm": arm, "title_style": title_style(chosen) if chosen else None}


def key_points_block(points: list) -> str:
    if not points:
        return ""
    return "**30 秒看重點**\n\n" + "\n".join(f"- {p}" for p in points) + "\n\n"
