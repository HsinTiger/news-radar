#!/usr/bin/env python3
"""每日公司分析選股（每天 11:25，由 substack_company job 串接 pick && compose 呼叫）：
從 company_pool.txt（S&P500 + Russell3000）挑「還沒分析過」的一家，寫進 .company_next，
並 append 到 .company_done（永久去重 → 每天不同公司、慢慢走完整個池，絕不重複）。

挑法：未分析過的公司裡，近 7 天新聞熱度最高者優先（夠即時）；都沒熱度 → 取池中下一個未分析的。
once-per-day guard：今天已挑過（.company_done 末筆是今天）→ 不重挑、exit 1，讓串接的 compose
跳過，避免「一天兩篇」（手動先跑一次 + 11:30 排程又跑）。
信哥要指定 → 改 .company_next 第一行 ticker；要重分析某股 → 從 .company_done 移除該行。
"""
from __future__ import annotations
import sqlite3, subprocess, sys, datetime
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
POOL_PATH = REPO / "substack_radar" / "config" / "company_pool.txt"
NEXT_PATH = REPO / "data" / "substack_drafts" / ".company_next"
DONE_PATH = REPO / "data" / "substack_drafts" / ".company_done"
DB = REPO / "data" / "01_harvest" / "news_radar.db"


def _load_pool():
    items, seen = [], set()
    if POOL_PATH.exists():
        for ln in POOL_PATH.read_text(encoding="utf-8").splitlines():
            if not ln.strip() or ln.lstrip().startswith("#"):
                continue
            parts = ln.split("\t")
            tk = parts[0].strip()
            nm = parts[1].strip() if len(parts) > 1 else tk
            if tk and tk not in seen:
                seen.add(tk); items.append((tk, nm))
    return items


def _done_rows():
    if not DONE_PATH.exists():
        return []
    return [l for l in DONE_PATH.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.lstrip().startswith("#")]


def main() -> int:
    today = datetime.date.today().isoformat()
    rows = _done_rows()
    if rows:
        last = rows[-1].split("\t")
        if len(last) >= 3 and last[2].strip() == today:
            print(f"[pick] 今天（{today}）已挑過公司，跳過（避免一天兩篇）。"); return 1
    items = _load_pool()
    if not items:
        print(f"[pick] 池空：{POOL_PATH}"); return 2
    done = {r.split('\t')[0].split()[0] for r in rows}
    undone = [(tk, nm) for tk, nm in items if tk not in done]
    if not undone:
        print(f"[pick] 整個池（{len(items)} 家）都分析過了——補池或清 .company_done。"); return 3

    heat: Counter = Counter()
    if DB.exists():
        try:
            conn = sqlite3.connect(str(DB))
            blob = " ".join((t or "") + " " + (b or "") for t, b in conn.execute(
                "SELECT title, substr(clean_markdown,1,600) FROM news_items "
                "WHERE julianday('now') - julianday(fetched_at) < 7").fetchall()).lower()
            conn.close()
            for tk, nm in undone:
                key = nm.lower()
                if len(key) >= 4:
                    heat[tk] = blob.count(key)
        except Exception as e:
            print(f"[pick] 熱度略過：{e}")

    hot = [x for x in undone if heat[x[0]] > 0]
    tk, nm = max(hot, key=lambda x: heat[x[0]]) if hot else undone[0]
    NEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
    NEXT_PATH.write_text(
        "# 今日公司分析（每天 11:30 自動）。改第一行的 ticker 可指定要分析的公司。\n"
        f"{tk}    # {nm}（近7天新聞熱度 {heat[tk]}）\n", encoding="utf-8")
    with open(DONE_PATH, "a", encoding="utf-8") as f:
        f.write(f"{tk}\t{nm}\t{today}\n")
    print(f"[pick] 今日：{tk}（{nm}）熱度 {heat[tk]} | 池 {len(items)}・已分析 {len(done)+1}・剩 {len(undone)-1}")
    try:
        subprocess.run(["osascript", "-e",
            f'display notification "今日：{tk} {nm}" with title "News Radar 每日財報分析"'], timeout=10)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
