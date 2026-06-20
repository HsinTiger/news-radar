#!/usr/bin/env python3
"""每週公司分析的半自動選股（週六跑）：watchlist × 本週新聞熱度 → 提 2-3 候選。

寫進 data/substack_drafts/.company_next：第一行（非 # 開頭）的 ticker = 週日 09:00 要分析的公司。
信哥若想換，把想要的 ticker 移到第一行即可；不動 → 自動取候選 top。compose.py company 會讀這檔。
"""
from __future__ import annotations
import sqlite3, subprocess, sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WL_PATH = REPO / "substack_radar" / "config" / "company_watchlist.yaml"
NEXT_PATH = REPO / "data" / "substack_drafts" / ".company_next"
DB = REPO / "data" / "01_harvest" / "news_radar.db"


def main() -> int:
    import yaml
    wl = (yaml.safe_load(WL_PATH.read_text(encoding="utf-8")) or {}).get("watchlist", [])
    items = [(w["ticker"], w["name"], [w["name"]] + list(w.get("aliases", []))) for w in wl]

    # 本週新聞熱度：每間公司在近 7 天 news_items 出現次數
    heat: Counter = Counter()
    if DB.exists():
        try:
            conn = sqlite3.connect(str(DB))
            rows = conn.execute(
                "SELECT title, substr(clean_markdown,1,600) FROM news_items "
                "WHERE julianday('now') - julianday(fetched_at) < 7"
            ).fetchall()
            conn.close()
            blob = " ".join((t or "") + " " + (b or "") for t, b in rows).lower()
            for tk, _name, aliases in items:
                # 只用夠長、夠獨特的別名（避免 NOW→"now"、COIN→"coin" 這種短字串噪音）
                keys = {k.lower() for k in aliases if len(str(k)) >= 4}
                heat[tk] = sum(blob.count(k) for k in keys)
        except Exception as e:
            print(f"[pick] ⚠️ 熱度計算略過：{e}")

    # 排除最近 8 週已分析過的（讀 .company_done）
    done = set()
    done_log = REPO / "data" / "substack_drafts" / ".company_done"
    if done_log.exists():
        done = {l.split()[0] for l in done_log.read_text(encoding="utf-8").splitlines() if l.strip()}

    ranked = sorted(items, key=lambda x: -heat[x[0]])
    cands = [c for c in ranked if c[0] not in done][:3] or ranked[:3]

    lines = [
        "# 本週公司分析候選（依本週新聞熱度排序）。",
        "# 把你要分析的 ticker 放到第一行（非 # 開頭）即可；不改 → 週日 09:00 自動取下面第一個。",
        "",
    ]
    for i, (tk, name, _a) in enumerate(cands):
        prefix = "" if i == 0 else "# "
        lines.append(f"{prefix}{tk}    # {name}（本週新聞熱度 {heat[tk]}）")
    NEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
    NEXT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    msg = "本週候選：" + "、".join(f"{tk}({name})" for tk, name, _ in cands)
    print(f"[pick] {msg}\n[pick] 寫入 {NEXT_PATH}（改第一行可換股）")
    try:
        subprocess.run(["osascript", "-e",
                        f'display notification "{msg}" with title "News Radar 週日公司分析候選"'],
                       timeout=10)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
