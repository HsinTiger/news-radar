#!/usr/bin/env python3
"""每週公司分析選股（週日 09:00 compose job 的第一步）：
從 company_pool.txt（S&P500 + Russell3000）挑「還沒分析過」的一家，寫進 .company_next，
並 append 到 .company_done（永久去重 → 每週不同公司、慢慢走完整個池，絕不重複）。

挑法：未分析過的公司裡，近 7 天新聞熱度最高者優先（夠即時）；都沒熱度 → 取池中下一個未分析的。
same-day：worker 只在今天還沒有通過稽核的公司草稿時才呼叫本程式；今天挑過的那家沒成功 → 換一家（一天最多 3 家）。
信哥要指定 → 在排程前改 .company_next 第一行 ticker；要重分析某股 → 從 .company_done 移除該行。
"""
from __future__ import annotations
import json, os, re, sqlite3, subprocess, sys, datetime
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
# 2026-11-20 主編：主題不變，從台灣讀者切入。熱度原本只用英文公司名去數，台灣媒體寫的是中文名（輝達、美光），
# 等於完全沒算到台灣讀者在看什麼。台灣媒體提到的次數乘 3 再加進去。
TW_FEEDS = ("TechNews 科技新報", "中央社 財經", "中央社 國際", "公視新聞 PTS")
TW_WEIGHT = 3
POOL_PATH = REPO / "substack_radar" / "config" / "company_pool.txt"
NEXT_PATH = REPO / "data" / "substack_drafts" / ".company_next"
DONE_PATH = REPO / "data" / "substack_drafts" / ".company_done"
DB = REPO / "data" / "01_harvest" / "news_radar.db"
FAILED_PATH = REPO / "data" / "substack_drafts" / ".company_failed"


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


def _failed_rows():
    if not FAILED_PATH.exists():
        return []
    return [l for l in FAILED_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]


def _failed_count(day: str) -> int:
    return sum(1 for r in _failed_rows() if r.split("\t")[-1].strip() == day)


def _clean_company_draft_on(day: str) -> bool:
    """那天有沒有「通過事實稽核」的系列上篇（metadata 裡 source.series_part == 1）。
    系列上線前寫的單篇（例如 2026-10-17 的嬌生）不算，隔天不會硬接一篇下篇。"""
    folder = REPO / "data" / "substack_drafts" / day
    for meta in folder.glob("company_*/metadata.json"):
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (m.get("source") or {}).get("series_part") != 1:
            continue
        if not any(str(w).startswith("[品質迴圈未通過]") for w in m.get("audit_warnings") or []):
            return True
    return False


def _series_part2(today: str, rows: list[str]) -> tuple[str, str] | None:
    """今天是週日、昨天的上篇寫成了 → 回 (ticker, 名稱)；下篇今天已失敗 2 次就放棄。"""
    day = datetime.date.fromisoformat(today)
    if day.weekday() != 6:
        return None
    yesterday = (day - datetime.timedelta(days=1)).isoformat()
    sat = [r.split("\t") for r in rows if len(r.split("\t")) >= 3 and r.split("\t")[2].strip() == yesterday
           and not r.rstrip().endswith("\tpart2")]
    if not sat or not _clean_company_draft_on(yesterday):
        return None
    tries = sum(1 for r in rows if r.rstrip().endswith("\tpart2") and r.split("\t")[2].strip() == today)
    if tries >= 2:
        print("[pick] 下篇今天已試 2 次沒成功，改挑一家新公司寫單篇。")
        return None
    return sat[-1][0].split()[0], sat[-1][1].strip()


def taiwan_heat(undone, blob_tw: str) -> Counter:
    """台灣媒體近 7 天提到這家公司中文名的次數。只算中文別名：短英文名（HP、GE）在中文稿裡會誤中。"""
    from substack_radar.company_financials import _TITLE_ALIASES

    out: Counter = Counter()
    for tk, _nm in undone:
        zh = [a for a in _TITLE_ALIASES.get(tk, ()) if re.search(r"[\u4e00-\u9fff]", a)]
        if zh:
            out[tk] = sum(blob_tw.count(a) for a in zh)
    return out


def main(argv=None) -> int:
    dry_run = "--dry-run" in (argv if argv is not None else sys.argv[1:])
    today = datetime.date.today().isoformat()
    rows = _done_rows()
    # 指定公司重寫：COMPANY_FORCE=JNJ（2026-10-17：嬌生來源夠、是寫稿那段出問題，要重寫同一家，不是換一家）
    force = os.environ.get("COMPANY_FORCE", "").strip().upper()
    if force:
        name = dict(_load_pool()).get(force, force)
        NEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
        NEXT_PATH.write_text(f"# 指定重寫（COMPANY_FORCE）\n{force}    # {name}\n", encoding="utf-8")
        if not any(r.split("\t")[0] == force for r in rows):
            with open(DONE_PATH, "a", encoding="utf-8") as f:
                f.write(f"{force}\t{name}\t{today}\n")
        print(f"[pick] 指定：{force}（{name}）")
        return 0
    # 週末兩篇系列（2026-10-17 信哥拍板，對標曼報 Pro「商業解碼」一家公司寫多篇）：
    # 週六寫上篇「商業模式地圖」，週日用同一家寫下篇「財務與評價」。
    # 週六沒產出可發的上篇，週日就當一般單篇重新挑。下篇今天失敗 2 次才放棄、改挑新公司。
    series = _series_part2(today, rows)
    if series:
        tk, name = series
        NEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
        NEXT_PATH.write_text(f"# 週末系列下篇（財務與評價）part=2\n{tk}    # {name}\n", encoding="utf-8")
        with open(DONE_PATH, "a", encoding="utf-8") as f:
            f.write(f"{tk}\t{name}\t{today}\tpart2\n")
        print(f"[pick] 週末系列下篇：{tk}（{name}）")
        return 0
    # 走到這裡代表今天還沒有「通過稽核」的公司草稿（worker 的 guard 已先擋掉成功的情況）。
    # 2026-10-17：PepsiCo 06:00 只找到 3 份可用來源（要 5 份）失敗，舊規則 13:00 重試又挑回
    # PepsiCo → 同樣失敗、週六沒稿。改成：今天挑過但失敗的公司換一家；失敗的那家從 done
    # 移掉、記到 .company_failed，之後某天還能再輪到它。一天最多試 3 家。
    failed_today = [r for r in rows if len(r.split("\t")) >= 3 and r.split("\t")[2].strip() == today
                    and not r.rstrip().endswith("\tpart2")]
    if failed_today:
        if len(failed_today) + _failed_count(today) >= 3:
            print(f"[pick] 今天已試過 {len(failed_today) + _failed_count(today)} 家都沒成功，不再換。"); return 1
        tickers = [r.split("\t")[0].split()[0] for r in failed_today]
        rows = [r for r in rows if r not in failed_today]
        DONE_PATH.write_text("".join(f"{r}\n" for r in rows), encoding="utf-8")
        with open(FAILED_PATH, "a", encoding="utf-8") as f:
            for tk in tickers:
                f.write(f"{tk}\t{today}\n")
        print(f"[pick] 今天挑的 {', '.join(tickers)} 沒產出可發的稿，換一家。")
    skip_today = {r.split("\t")[0] for r in _failed_rows() if r.split("\t")[-1].strip() == today}
    items = _load_pool()
    if not items:
        print(f"[pick] 池空：{POOL_PATH}"); return 2
    done = {r.split('\t')[0].split()[0] for r in rows}
    undone = [(tk, nm) for tk, nm in items if tk not in done and tk not in skip_today]
    if not undone:
        print(f"[pick] 整個池（{len(items)} 家）都分析過了——補池或清 .company_done。"); return 3

    heat: Counter = Counter()
    tw: Counter = Counter()
    if DB.exists():
        try:
            conn = sqlite3.connect(str(DB))
            blob = " ".join((t or "") + " " + (b or "") for t, b in conn.execute(
                "SELECT title, substr(clean_markdown,1,600) FROM news_items "
                "WHERE julianday('now') - julianday(fetched_at) < 7").fetchall()).lower()
            marks = ",".join("?" * len(TW_FEEDS))
            blob_tw = " ".join((t or "") + " " + (b or "") for t, b in conn.execute(
                "SELECT title, substr(clean_markdown,1,600) FROM news_items "
                f"WHERE julianday('now') - julianday(fetched_at) < 7 AND feed_name IN ({marks})", TW_FEEDS).fetchall())
            conn.close()
            for tk, nm in undone:
                key = nm.lower()
                if len(key) >= 4:
                    heat[tk] = blob.count(key)
            tw = taiwan_heat(undone, blob_tw)
        except Exception as e:
            print(f"[pick] 熱度略過：{e}")

    score = lambda x: heat[x[0]] + TW_WEIGHT * tw[x[0]]
    hot = [x for x in undone if score(x) > 0]
    tk, nm = max(hot, key=score) if hot else undone[0]
    if dry_run:
        for x in sorted(hot, key=score, reverse=True)[:8]:
            print(f"[pick] 候選 {x[0]:6s} {x[1][:28]:28s} 英文熱度 {heat[x[0]]:3d}｜台灣媒體 {tw[x[0]]:3d}")
        print(f"[pick]（試跑，不寫檔）會選：{tk}（{nm}）")
        return 0
    NEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
    NEXT_PATH.write_text(
        "# 本週公司分析（週日 09:00 自動建草稿）。改第一行 ticker 可指定公司。\n"
        f"{tk}    # {nm}（近7天新聞熱度 {heat[tk]}，台灣媒體 {tw[tk]}）\n", encoding="utf-8")
    with open(DONE_PATH, "a", encoding="utf-8") as f:
        f.write(f"{tk}\t{nm}\t{today}\n")
    print(f"[pick] 今日：{tk}（{nm}）熱度 {heat[tk]} | 池 {len(items)}・已分析 {len(done)+1}・剩 {len(undone)-1}")
    try:
        subprocess.run(["osascript", "-e",
            f'display notification "本週候選：{tk} {nm}" with title "News Radar 週報公司分析"'], timeout=10)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
