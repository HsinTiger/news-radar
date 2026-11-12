"""投稿說明（Hsin選觀點，2026-11-13）。

投稿頁原本只有一個「備註」欄，後端把它同時當成文章來源的標題和給寫手的指示：
文章開頭於是印出「本篇根據：[寫一篇主觀上的感性抒發…](網址)」，備註有換行連結就壞掉。
現在分成兩件事：
- 角度：信哥希望從哪裡切、不要寫成什麼。
- 我的想法：信哥自己的話與經歷。**文章裡的個人經歷只能來自這裡**，寫手不得自己編。
兩者都只給寫手看，永遠不當標題、不進文章的來源連結。

存放方式：包成一行 JSON 的 HTML 註解，放在 news_items.clean_markdown 最前面；
compose 讀出來之後從素材裡拿掉。這個檔案不依賴其他模組，雲端投稿腳本也會 import。
"""
from __future__ import annotations

import json
import re

_BLOCK = re.compile(r"<!--HSIN_BRIEF (\{.*?\}) HSIN_BRIEF-->\n*", re.S)
_ANGLE_MARK, _VIEW_MARK = "【角度】", "【我的想法】"


def split_note(note: str) -> tuple[str, str]:
    """投稿頁把兩欄合成一段送來：「【角度】…【我的想法】…」。沒有標記的舊備註整段當角度。"""
    text = (note or "").strip()
    if _VIEW_MARK in text:
        head, _, view = text.partition(_VIEW_MARK)
        return head.replace(_ANGLE_MARK, "").strip(), view.strip()
    return text.replace(_ANGLE_MARK, "").strip(), ""


def brief_block(note: str) -> str:
    angle, view = split_note(note)
    if not angle and not view:
        return ""
    return "<!--HSIN_BRIEF " + json.dumps({"angle": angle, "view": view}, ensure_ascii=False) + " HSIN_BRIEF-->\n\n"


def extract(body: str) -> tuple[dict, str]:
    """回 ({"angle","view"}, 拿掉說明區塊後的素材)。沒有區塊回 ({}, 原文)。"""
    m = _BLOCK.search(body or "")
    if not m:
        return {}, body or ""
    try:
        data = json.loads(m.group(1))
    except ValueError:
        data = {}
    return ({"angle": str(data.get("angle") or ""), "view": str(data.get("view") or "")},
            _BLOCK.sub("", body, count=1))


def writer_mandate(brief: dict) -> str:
    """給寫手的指示（接在 editorial_note）。"""
    angle, view = (brief.get("angle") or "").strip(), (brief.get("view") or "").strip()
    lines = ["這是主編 Hsin 親自投稿的題材。素材只是引子：不要翻譯或摘要它，"
             "要從它出發自己找資料、延伸、寫成一篇跟原文不像的原創文章。"]
    if angle:
        lines.append(f"主編希望的角度（照做，但不要把這段話抄進文章）：{angle}")
    if view:
        lines.append("主編自己的想法與經歷（文章的主幹，可以用第一人稱寫進去；"
                     f"個人經歷只能用這裡寫的，不得另外編造）：{view}")
    else:
        lines.append("主編這次沒有提供個人經歷：文章裡不要出現任何「我曾經…」「我買過…」這類個人經歷，"
                     "只能寫看完資料之後的理解與提問。")
    return "\n".join(lines)
