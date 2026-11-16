#!/usr/bin/env python3
"""一次性：把舊 log 裡的 [Review] 退稿意見補進 logs/writing/rejections.csv（2026-11-16）。

/tmp 的 log 重開機就沒了，先把這一個月的退稿原因存成帳本，substack_radar.lessons 才有東西可學。
重跑安全：帳本裡已經有同一個 run 的就跳過。用法：.venv/bin/python scripts/backfill_rejections.py [log...]
"""
from __future__ import annotations

import csv
import glob
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from src.run_log import LOG_DIR, REJECTION_FIELDS, redact  # noqa: E402

_STYLE = re.compile(r"^(AI 套話|英文沒翻譯|小標像標籤|同一個論點|查證過程|文章中間插了|「.{2,30}」出現 \d+ 次)")


def parse(path: str) -> list[dict]:
    rows, pending, rnd = [], [], 0
    for line in open(path, encoding="utf-8", errors="ignore"):
        m = re.match(r"\[Review\] 第 (\d) 次審稿", line)
        if m:
            rnd = int(m.group(1))
            continue
        if line.startswith("[Review]   - ") and rnd:
            body = line[len("[Review]   - "):].strip()
            if body.startswith("[程式檢查]"):
                kind = body[len("[程式檢查]"):].strip()
                pending.append((rnd, "程式", kind.split("：")[0], kind))
            elif body.startswith("[審稿]"):
                text = body[len("[審稿]"):].strip()
                problem, _, rest = text.partition("｜原句：")
                source = "篇幅" if problem.startswith("篇幅") else ("文風" if _STYLE.match(problem) else "審稿")
                pending.append((rnd, source, problem, rest.split("｜建議改法")[0]))
            continue
        m = re.match(r"\[Files\] wrote .*/substack_drafts/(\d{4}-\d{2}-\d{2})/([a-z]+)_(.*)$", line)
        if m and pending:
            day, mode, slug = m.group(1), m.group(2), m.group(3).strip()
            if mode == "podcast" and "智取未來" in slug:
                mode = "podcast/ai"
            run = f"{day} backfill-{abs(hash(slug)) % 10000:04d} {mode}"
            rows += [{"date": day, "run": run, "round": r, "source": s, "kind": redact(k)[:60],
                      "quote": redact(q)[:120]} for r, s, k, q in pending]
            pending, rnd = [], 0
    return rows


def main() -> int:
    files = sys.argv[1:] or (glob.glob("/tmp/news-radar-*.out.log")
                             + glob.glob(str(REPO / "logs" / "launchd_substack_drain*.log")))
    out = LOG_DIR / "rejections.csv"
    have = set()
    if out.exists():
        with open(out, encoding="utf-8", newline="") as f:
            have = {(r["date"], r["round"], r["kind"]) for r in csv.DictReader(f)}
    rows = [r for f in files for r in parse(f)]
    rows = [r for r in rows if (r["date"], str(r["round"]), r["kind"]) not in have]
    rows.sort(key=lambda r: (r["date"], r["run"], r["round"]))
    out.parent.mkdir(parents=True, exist_ok=True)
    new_file = not out.exists()
    with open(out, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REJECTION_FIELDS)
        if new_file:
            w.writeheader()
        w.writerows(rows)
    print(f"[backfill] 補了 {len(rows)} 條退稿記錄 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
