"""把過去的退稿原因變成下一篇的交稿前自查（2026-11-16，信哥：根據過往經驗讓寫手少被退）。

資料來源是 logs/writing/rejections.csv（審稿迴圈每一輪記的帳）。只取「第一輪」「程式檢查／篇幅／文風」
這三種：它們有固定的名字、固定的避免方法，而且是寫手第一稿就能自己避開的。審稿模型的事實意見
每篇都不一樣，不適合變成通則。

自我修正：某個原因最近不再出現，就會自動從提醒裡消失；提醒最多 4 條，不會越堆越多。
"""
from __future__ import annotations

import csv
import re
from collections import Counter, OrderedDict
from pathlib import Path

MAX_LESSONS = 4
RECENT_RUNS = 12
MIN_RUNS = 2          # 至少在兩篇出現過才算「常犯」

# (辨識用的 pattern, 固定名稱, 怎麼避免)
_KINDS = [
    (r"^E1", "出處對不上", "寫「根據 X」「X 指出」時，X 要是取材清單裡真的有的媒體或機構；"
                         "節目裡的人直接寫名字（「Alex 說」），不要寫成聽起來像外部機構的說法。"),
    (r"^E3|數字無法定位", "數字找不到出處", "帶單位的數字（%、億、倍）要跟素材裡的一模一樣；自己換算的，把算法寫出來。"),
    (r"^A1b|標籤句", "開場貼標籤", "開場不要寫「這篇要回答的是…」「我的判斷是：」這種宣告句，判斷藏在敘事裡。"),
    (r"^A1", "開場太長", "第一個小標之前 10 句內，就要讓讀者看出這篇在談什麼問題、你傾向哪邊。"),
    (r"^A2", "小標後第一句用代名詞", "每個小標後的第一句要寫出主詞（誰、哪家公司），不要用「他」「這」開頭。"),
    (r"^A3", "數據沒標時間", "重要數字要帶年份或季度。"),
    (r"篇幅不足", "寫太短", "字數只算中文字（英文、數字、網址不算）。把主角是誰、在什麼處境、事情怎麼一步步發展、"
                         "具體的例子寫完整，直接寫到字數上限附近。"),
    (r"套話", "AI 套話", "不寫「全集最值得」「我的判斷是」「最後留一個問題給你」這類每篇都有的句子。"),
    (r"英文沒翻譯", "英文沒翻譯", "英文片語翻成自然的中文；要留原文就放括號，只放第一次。"),
    (r"小標像標籤", "小標像標籤", "小標寫成一句完整的話或讀者心裡的疑問，不要兩三個字的標籤。"),
    (r"查證過程", "查證過程寫進文章", "正文不出現「素材」「逐字稿」「研究來源」「提供的來源」；查不到的就不寫。"),
    (r"出現 \d+ 次", "每句都冠節目名", "節目名在開頭講一次，之後寫「他說」「主持人追問」。"),
    (r"專有名詞註解", "名詞清單", "不做「專有名詞註解」清單，名詞第一次出現時在句子裡順口解釋。"),
    (r"同一個論點", "重複論點", "同一個論點只講一次，第二次出現時要推進到下一步。"),
    (r"中文數字|數字量級", "數字寫法", "數字用阿拉伯數字，億與兆的量級寫之前再對一次素材。"),
    (r"對仗", "對仗句太多", "「不是 X，而是 Y」一篇最多用一兩次，其餘直接講 Y。"),
    (r"比較無基準", "比較沒講基準", "寫「更快」「更貴」「大幅」時，講清楚跟什麼比、差多少。"),
    (r"段落過長", "段落太長", "一段超過 260 字就拆開，一段只講一件事。"),
    (r"填充詞|修辭黑名單|收尾 AI 味|破折號|名詞化", "AI 手癖", "少用破折號與填充詞（其實、很清楚、值得注意），動作不要藏進抽象名詞。"),
]


def classify(kind: str) -> tuple[str, str] | None:
    for pat, name, advice in _KINDS:
        if re.search(pat, kind or ""):
            return name, advice
    return None


def _mode(run: str) -> str:
    return (run or "").split(" ")[-1]


def load(path: Path | None = None) -> list[dict]:
    if path is None:
        from src.run_log import LOG_DIR
        path = LOG_DIR / "rejections.csv"
    try:
        with open(path, encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def top_lessons(mode: str, *, rows: list[dict] | None = None, limit: int = MAX_LESSONS) -> list[tuple[str, str, int, int]]:
    """回 [(名稱, 怎麼避免, 幾篇犯過, 看了幾篇)]，同專欄樣本不到 3 篇就用全部專欄的。"""
    rows = load() if rows is None else rows
    first = [r for r in rows if str(r.get("round")) == "1" and r.get("source") in ("程式", "篇幅", "文風")]

    def tally(pool: list[dict]):
        runs = list(OrderedDict.fromkeys(r.get("run", "") for r in pool))[-RECENT_RUNS:]
        seen: Counter = Counter()
        advice: dict[str, str] = {}
        for run in runs:
            names = set()
            for r in pool:
                if r.get("run") != run:
                    continue
                hit = classify(r.get("kind", ""))
                if hit:
                    names.add(hit[0])
                    advice[hit[0]] = hit[1]
            seen.update(names)
        return runs, seen, advice

    same = [r for r in first if _mode(r.get("run", "")) == mode]
    runs, seen, advice = tally(same)
    if len(runs) < 3:
        runs, seen, advice = tally(first)
    return [(name, advice[name], n, len(runs)) for name, n in seen.most_common() if n >= MIN_RUNS][:limit]


def prompt_block(mode: str, *, rows: list[dict] | None = None) -> str:
    try:
        lessons = top_lessons(mode, rows=rows)
    except Exception:
        return ""
    if not lessons:
        return ""
    lines = [f"- {name}（最近 {total} 篇有 {n} 篇）：{advice}" for name, advice, n, total in lessons]
    return ("=== 交稿前自查（最近幾篇第一稿實際被退的原因，由程式統計）===\n"
            "這幾件事避開了，稿子通常一次就過：\n" + "\n".join(lines) + "\n\n")


if __name__ == "__main__":
    import sys
    for m in sys.argv[1:] or ["podcast", "podcast/ai", "company", "morning"]:
        print(f"[{m}]\n{prompt_block(m) or '（還沒有足夠的記錄）'}")
