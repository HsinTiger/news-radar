"""Substack Notes（2026-11-13）：每篇文章配一則站內動態，能接上當天稍早的時事就接。

站內是近 90 天唯一帶進新訂閱的來源，Notes 是站內給還沒訂閱的人看的入口。
流程（文章發布後由 daily_publish 呼叫）：
1. 從新聞庫挑今天跟這篇文章沾得上邊的標題（先用關鍵詞粗篩，免得把整庫丟給模型）。
2. 模型判斷有沒有「真的相關」的時事：有就用它開頭、帶到文章的觀點；沒有就用寫稿時備好的那則。
3. 程式把關：長度、不含網址與 hashtag、數字必須出自文章或那則新聞標題。不過就退回原本那則。
4. SUBSTACK_NOTES_AUTOPOST=1 時自動貼出；否則（或貼失敗）寄信請主編自己貼。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3

from substack_radar.headline_desk import _numbers, pangu

NEWS_FEEDS = ("TechNews 科技新報", "中央社 國際", "中央社 財經", "公視新聞 PTS", "TechCrunch AI",
              "The Verge", "Ars Technica", "Hacker News Front Page", "Motley Fool", "證交所 官方訊息")

_PROMPT = """你替一份中文電子報的作者寫一則 Substack Notes（站內動態，給還沒訂閱的人看）。

【今天發布的文章重點】
{article}

【寫稿時備好的 Notes】
{base}

【今天稍早的新聞標題】
{headlines}

判斷：上面有沒有哪一則新聞，跟這篇文章談的是同一件事或同一個問題？
- 有：用那則新聞開頭（寫出媒體名與發生了什麼），再帶到文章的觀點，最後一句留懸念。use 填那則的編號。
- 沒有真的相關的：不要硬湊，use 填 0，note 原樣回傳備好的那則。
規則：120–240 字；像作者本人隨手發的一段話；不要「歡迎訂閱」「點擊閱讀」、不要 hashtag、不要網址；
只能用文章與那則新聞標題裡有的事實與數字，新聞標題沒寫的細節不要自己補。

只輸出 JSON：{{"use": 0, "note": ""}}"""


def todays_headlines(article: str, *, db_path=None, hours: int = 18, limit: int = 12) -> list[tuple[str, str]]:
    """[(標題, 媒體)]：今天的新聞裡，標題跟文章有關鍵詞重疊的前幾則。"""
    from substack_radar.compose import NEWS_DB_PATH, _key_tokens

    try:
        conn = sqlite3.connect(str(db_path or NEWS_DB_PATH))
        try:
            marks = ",".join("?" * len(NEWS_FEEDS))
            rows = conn.execute(
                f"SELECT title, feed_name FROM news_items WHERE feed_name IN ({marks}) "
                f"AND published_at >= datetime('now','-{int(hours)} hours') AND source_type != 'video' "
                "ORDER BY published_at DESC LIMIT 600", NEWS_FEEDS).fetchall()
        finally:
            conn.close()
    except Exception as exc:
        print(f"[Notes] ⚠️ 讀不到今天的新聞：{str(exc)[:80]}")
        return []
    art = (article or "").lower()
    scored = []
    for title, feed in rows:
        hits = [t for t in _key_tokens(title or "") if t in art]
        score = sum(2 if t.isascii() and len(t) >= 4 else 1 for t in hits)
        if score >= 3:
            scored.append((score, title, feed))
    scored.sort(key=lambda x: -x[0])
    return [(t, f) for _, t, f in scored[:limit]]


_NOTE_FORMULA = re.compile(r"什麼(?:條件|情況)下[^。？！?]{0,8}(?:改|調整|認錯)[^。？！?]*[？?]|最後留一個問題|歡迎訂閱|點擊閱讀")


def clean_note(note: str) -> str:
    """拿掉 Notes 裡的 AI 套話（2026-11-20：機器人那篇的 Notes 寫「我的判斷是：…」，
    最後用「什麼條件下我會改這個判斷？」收尾——正文早就不准這樣寫，Notes 卻沒人檢查）。
    標籤式的「我的判斷是：」改成口語的「我目前傾向認為，」；固定反問和其他套話句整句拿掉。"""
    from substack_radar.style_gate import STOCK_PHRASES, _STOCK_PATTERNS

    text = re.sub(r"我的判斷是[：:]\s*", "我目前傾向認為，", (note or "").strip())
    out = []
    for para in text.split("\n"):
        sentences = re.split(r"(?<=[。！？?])", para)
        keep = [x for x in sentences if x.strip() and not _NOTE_FORMULA.search(x)
                and not any(p in x for p in STOCK_PHRASES) and not any(pat.search(x) for pat, _ in _STOCK_PATTERNS)]
        if "".join(keep).strip():
            out.append("".join(keep).strip())
    return "\n\n".join(out)


def note_ok(note: str, allowed_text: str) -> bool:
    return (80 <= len(note) <= 360 and not re.search(r"https?://|#\S", note)
            and _numbers(note) <= _numbers(allowed_text))


def build(*, article: str, base_note: str, headlines: list[tuple[str, str]] | None = None, chat=None) -> dict:
    """回 {"note": 文字, "news": 用到的新聞標題或 ""}。任何失敗都退回 base_note。"""
    base_note = clean_note(base_note or "")
    headlines = todays_headlines(article) if headlines is None else headlines
    if not headlines:
        return {"note": base_note, "news": ""}
    if chat is None:
        from substack_radar.quality_loop import _oss_chat

        def chat(prompt):
            return _oss_chat("audit", prompt, 600)
    try:
        from src.llm_brain import _extract_json_blob

        listing = "\n".join(f"[{i}] {t}（{f}）" for i, (t, f) in enumerate(headlines, 1))
        raw = chat(_PROMPT.format(article=(article or "")[:6000], base=base_note or "（沒有）", headlines=listing))
        data = json.loads(_extract_json_blob(raw) or "{}")
        use = int(data.get("use") or 0)
        note = clean_note(pangu(str(data.get("note") or "").strip()))
    except Exception as exc:
        print(f"[Notes] ⚠️ 時事版本失敗，用原本那則：{str(exc)[:100]}")
        return {"note": base_note, "news": ""}
    if not (1 <= use <= len(headlines)):
        return {"note": base_note, "news": ""}
    news_title = headlines[use - 1][0]
    if not note_ok(note, article + " " + news_title):
        print("[Notes] ⏭️ 時事版本沒過檢查（長度、網址或數字對不上），用原本那則")
        return {"note": base_note, "news": ""}
    print(f"[Notes] 🗞️ 接上今天的時事：{news_title[:50]}")
    return {"note": note, "news": news_title}


def post_note(text: str, link: str, *, session=None) -> str | None:
    """把 Notes 貼到 Substack（附文章連結卡片）。回 Notes 網址；失敗回 None，不丟例外。"""
    try:
        if session is None:
            from substack import Api

            session = Api(cookies_string=os.environ["SUBSTACK_COOKIES_STRING"],
                          publication_url=os.environ["SUBSTACK_PUBLICATION_URL"])._session
        attachment_ids = []
        if link:
            r = session.post("https://substack.com/api/v1/comment/attachment",
                             json={"url": link, "type": "link"}, timeout=30)
            if r.ok and r.json().get("id"):
                attachment_ids.append(r.json()["id"])
        paragraphs = [{"type": "paragraph", "content": [{"type": "text", "text": p}]}
                      for p in re.split(r"\n+", text.strip()) if p.strip()]
        r = session.post("https://substack.com/api/v1/comment/feed", timeout=30, json={
            "bodyJson": {"type": "doc", "attrs": {"schemaVersion": "v1"}, "content": paragraphs},
            "tabId": "for-you", "surface": "feed", "replyMinimumRole": "everyone",
            **({"attachmentIds": attachment_ids} if attachment_ids else {}),
        })
        if not r.ok:
            print(f"[Notes] ❌ 貼不出去（HTTP {r.status_code}）：{r.text[:120]}")
            return None
        note_id = r.json().get("id")
        return f"https://substack.com/note/c-{note_id}" if note_id else "https://substack.com/notes"
    except Exception as exc:
        print(f"[Notes] ❌ 貼不出去：{str(exc)[:120]}")
        return None
