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

【一】寫 5 個標題候選，每個 ≤15 個字：
- 讓讀者對號入座：寫他的處境、他的錢、他的工作會怎樣，或一個違反直覺的反差、一個具體數字。
- 不要冒號、不要「｜」、不要節目名、不要問「你知道嗎」這類空問句；英文詞一律完整，不要截斷。
- 只能用文章裡有的事實，不誇大。
然後選出你認為點閱率最高的一個（best 填它的序號 1–5）。

【二】寫「30 秒看重點」三條，每條 ≤40 字：
- 只滑到這裡就離開的讀者，也能帶走這篇最重要的三件事。
- 寫結論本身（誰、做了什麼、代表什麼），不寫「本文將…」。數字必須跟文章完全一致。

只輸出 JSON：{{"titles": ["", "", "", "", ""], "best": 1, "key_points": ["", "", ""]}}

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
        return opencc.OpenCC("s2tw").convert(text or "")
    except Exception:
        return text or ""


def pangu(text: str) -> str:
    """簡轉繁，中英文之間加空格（「AI代理」→「AI 代理」），跟正文寫法一致。"""
    text = _to_traditional(text)
    text = re.sub(r"([\u4e00-\u9fff])([A-Za-z0-9%$])", r"\1 \2", text or "")
    return re.sub(r"([A-Za-z0-9%])([\u4e00-\u9fff])", r"\1 \2", text)


def _numbers(text: str) -> set:
    return set(re.findall(r"\d+(?:[.,]\d+)?", text or ""))


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
        out.append(pangu(p))
    return out[:3]


def polish(*, title: str, subtitle: str, body: str, column: str, chat=None) -> dict:
    """回 {"title": 新標題或 None, "key_points": [..]}。chat(prompt)->str 可注入測試。"""
    if chat is None:
        from substack_radar.quality_loop import _oss_chat

        def chat(prompt):
            return _oss_chat("audit", prompt, 600)
    try:
        from src.llm_brain import _extract_json_blob

        raw = chat(_PROMPT.format(column=column or "—", title=title, subtitle=subtitle or "",
                                  body=(body or "")[:14000]))
        data = json.loads(_extract_json_blob(raw) or "{}")
    except Exception as exc:
        print(f"[HeadlineDesk] ⚠️ 標題台失敗，保留原標題、不放重點：{str(exc)[:100]}")
        return {"title": None, "key_points": []}

    titles = [pangu(str(t).strip()) for t in data.get("titles") or [] if str(t).strip()]
    try:
        best = int(data.get("best") or 1)
    except (TypeError, ValueError):
        best = 1
    ordered = ([titles[best - 1]] if 1 <= best <= len(titles) else []) + titles
    chosen = next((t for t in ordered if title_ok(t)), None)
    for t in titles:
        print(f"[HeadlineDesk] 候選{'✅' if t == chosen else ('  ' if title_ok(t) else '❌')} {t}")
    points = grounded_points(data.get("key_points") or [], body)
    if len(points) < 2:
        points = []
    return {"title": chosen, "key_points": points}


def key_points_block(points: list) -> str:
    if not points:
        return ""
    return "**30 秒看重點**\n\n" + "\n".join(f"- {p}" for p in points) + "\n\n"
