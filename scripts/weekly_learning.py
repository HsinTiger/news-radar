"""每週學習迴圈（WO-3）：從過去一週的失敗紀錄學出幾條教訓，寫進對應角色的教訓清單，寄摘要給主編。

不訓練模型，只累積規則。流程：
  1. collect_cases：收糾錯台紀錄、審稿警告、退稿帳本，新案例 append 到 data/learning/cases.jsonl（id 去重）。
  2. categorize → propose_lessons（每類叫一次便宜模型）→ accept（程式檢查，全過才收）。
  3. 通過的寫進 substack_radar/config/lessons/{writer,researcher}.md。
  4. 寫完跑 `python -m pytest tests/unit -q`，不是全綠就還原教訓清單。
  5. 本週統計 append 到 data/learning/weekly.jsonl。
  6. 用 src.notify._dispatch 寄週報，subject 以「【每週學習】」開頭。

任何一步失敗都不會讓程式掛掉：印出警告，寄出已有的部分。

用法：
    python -m scripts.weekly_learning              # 看過去 7 天
    python -m scripts.weekly_learning --days 14
    python -m scripts.weekly_learning --dry-run    # 只印，不寫檔、不跑測試、不寄信
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from substack_radar import lessons  # noqa: E402

try:                                               # 寄信函式；匯入失敗不影響學習本身
    from src.notify import _dispatch  # noqa: E402
except Exception:                                  # noqa: BLE001
    _dispatch = None
_ORIGINAL_DISPATCH = _dispatch

SUBJECT_PREFIX = "【每週學習】"
TEST_TIMEOUT_S = 1800


def _warn(msg: str, warnings: list[str]) -> None:
    print(f"[WeeklyLearning] ⚠️ {msg}")
    warnings.append(msg)


# ---------------------------------------------------------------------------
# 檔案（案例庫、週報歷史）
# ---------------------------------------------------------------------------

def _append_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _known_ids(path: Path) -> set[str]:
    return {str(r.get("id")) for r in lessons.load_history(path) if r.get("id")}


def append_new_cases(cases: list[dict], path: Path) -> int:
    """把還沒記過的案例 append 到案例庫，回新增幾筆。"""
    known = _known_ids(path)
    fresh = [c for c in cases if c["id"] not in known]
    if fresh:
        _append_jsonl(path, fresh)
    return len(fresh)


# ---------------------------------------------------------------------------
# 回歸測試與還原
# ---------------------------------------------------------------------------

def run_unit_tests(root: Path) -> bool:
    """跑 python -m pytest tests/unit -q，全綠才回 True。"""
    proc = subprocess.run([sys.executable, "-m", "pytest", "tests/unit", "-q"], cwd=str(root),
                          capture_output=True, text=True, timeout=TEST_TIMEOUT_S)
    if proc.returncode != 0:
        print((proc.stdout or "")[-1500:])
    return proc.returncode == 0


def _snapshot(root: Path) -> dict[str, str | None]:
    snap: dict[str, str | None] = {}
    for role in lessons.ROLES:
        p = lessons.lesson_path(role, root=root)
        snap[role] = p.read_text(encoding="utf-8") if p.exists() else None
    return snap


def _restore(root: Path, snap: dict[str, str | None]) -> None:
    for role, text in snap.items():
        p = lessons.lesson_path(role, root=root)
        if text is None:
            p.unlink(missing_ok=True)
        else:
            p.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# 週報
# ---------------------------------------------------------------------------

def build_report(stat: dict, *, dry_run: bool, warnings: list[str]) -> str:
    lines = [f"每週學習週報（{stat['since']} ～ {stat['until']}）" + ("【試跑，沒有寫檔】" if dry_run else ""), ""]
    lines.append(f"本週案例數：{stat['total']}（新記進案例庫 {stat.get('new_cases', 0)} 件）")
    lines.append("各類別件數：" + ("、".join(f"{k} {v}" for k, v in stat["counts"].items()) or "（沒有案例）"))
    lines.append("")
    lines.append("採用的教訓：")
    adopted = stat["adopted"]
    lines += [f"- [{a['role']}] {a['lesson']}（{a['category']}）" for a in adopted] or ["- （本週沒有採用新教訓）"]
    if stat.get("reverted"):
        lines.append("※ 回歸測試沒過，已還原（上面的教訓這次沒有生效）。")
    lines.append("")
    lines.append("被擋的教訓：")
    lines += [f"- {b['category']}：{b['lesson']}　→ {b['reason']}" for b in stat["blocked"]] or ["- （沒有）"]
    lines.append("")
    lines.append("建議改成程式檢查的類別（連續 3 週每週 ≥3 件）：" + ("、".join(stat["recurring"]) or "（沒有）"))
    if warnings:
        lines += ["", "執行中的警告："] + [f"- {w}" for w in warnings]
    return "\n".join(lines)


def send_report(subject: str, body: str) -> None:
    import src.notify as notify_mod
    fn = _dispatch if _dispatch is not _ORIGINAL_DISPATCH else getattr(notify_mod, "_dispatch", None)
    fn = fn or _dispatch
    if fn is None:
        raise RuntimeError("找不到 src.notify._dispatch")
    fn(subject=subject, body_html=None, body_plain=body)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run(days: int = 7, *, dry_run: bool = False, root: Path = REPO, chat=None, today: date | None = None,
        run_tests=None, send=None) -> str:
    """回傳週報全文。每一步都包 try，壞了就記警告、繼續往下。"""
    root = Path(root)
    today = today or date.today()
    since, until = today - timedelta(days=max(days, 1) - 1), today   # 含今天共 days 天
    warnings: list[str] = []
    cases_path = root / "data" / "learning" / "cases.jsonl"
    weekly_path = root / "data" / "learning" / "weekly.jsonl"
    stat: dict = {"date": today.isoformat(), "since": since.isoformat(), "until": until.isoformat(), "total": 0,
                  "new_cases": 0, "counts": {}, "adopted": [], "blocked": [], "recurring": [], "reverted": False}

    # 1. 收案例
    cases: list[dict] = []
    try:
        cases = lessons.collect_cases(since, until, root=root)
        stat["total"] = len(cases)
        if not dry_run:
            stat["new_cases"] = append_new_cases(cases, cases_path)
    except Exception as exc:                       # noqa: BLE001
        _warn(f"收集案例失敗：{str(exc)[:120]}", warnings)

    # 2. 分類、提案、檢查
    proposals: list[dict] = []
    try:
        groups = lessons.categorize(cases)
        stat["counts"] = {k: len(v) for k, v in groups.items()}
        proposals = lessons.propose_lessons(groups, chat=chat)
    except Exception as exc:                       # noqa: BLE001
        _warn(f"分類或提案失敗：{str(exc)[:120]}", warnings)

    new_lines: dict[str, list[str]] = {}
    try:
        current = {role: lessons.read_lessons(role, root=root) for role in lessons.ROLES}
        for p in proposals:
            role = p["role"] if p.get("role") in lessons.ROLES else "writer"
            pool = [x for r in lessons.ROLES for x in current[r]]      # 跟所有角色的既有教訓比相似度
            ok, reason = lessons.accept(p["lesson"], pool)
            if not ok:
                stat["blocked"].append({"category": p["category"], "lesson": p["lesson"], "reason": reason})
                continue
            line = lessons.format_line(p["lesson"], p["category"], today.isoformat())
            current[role].append(line)
            new_lines.setdefault(role, []).append(line)
            stat["adopted"].append({"role": role, "category": p["category"], "lesson": p["lesson"]})
    except Exception as exc:                       # noqa: BLE001
        _warn(f"檢查教訓失敗：{str(exc)[:120]}", warnings)
        new_lines, stat["adopted"] = {}, []

    # 3. 寫進清單，4. 跑回歸測試，不全綠就還原
    if new_lines and not dry_run:
        snap: dict[str, str | None] = {}
        try:
            snap = _snapshot(root)
            for role, added in new_lines.items():
                lessons.save_lessons(role, lessons.read_lessons(role, root=root) + added, root=root)
            passed = (run_tests or run_unit_tests)(root)
            if not passed:
                _restore(root, snap)
                stat["reverted"] = True
                _warn("回歸測試沒過，已還原", warnings)
        except Exception as exc:                   # noqa: BLE001
            _warn(f"寫入教訓或跑測試失敗：{str(exc)[:120]}，已還原", warnings)
            try:
                _restore(root, snap)
            except Exception as exc2:              # noqa: BLE001
                _warn(f"還原也失敗：{str(exc2)[:120]}", warnings)
            stat["reverted"] = True
        if stat["reverted"]:
            stat["blocked"] += [{"category": a["category"], "lesson": a["lesson"], "reason": "回歸測試沒過，已還原"}
                                for a in stat["adopted"]]
            stat["adopted"] = []

    # 5. 本週統計與「連續 3 週」判斷
    try:
        rows = lessons.load_history(weekly_path)
        stat["recurring"] = lessons.recurring_from_rows(rows + [stat])
        if not dry_run:
            _append_jsonl(weekly_path, [stat])
    except Exception as exc:                       # noqa: BLE001
        _warn(f"寫週報歷史失敗：{str(exc)[:120]}", warnings)

    # 6. 週報
    report = build_report(stat, dry_run=dry_run, warnings=warnings)
    print(report)
    if dry_run:
        print("[WeeklyLearning] --dry-run：沒有寫檔、沒有寄信")
        return report
    try:
        subject = f"{SUBJECT_PREFIX}{today.isoformat()} 案例 {stat['total']} 件、採用 {len(stat['adopted'])} 條"
        (send or send_report)(subject, report)
    except Exception as exc:                       # noqa: BLE001
        _warn(f"寄週報失敗：{str(exc)[:120]}", warnings)
    return report


def main(argv: list[str] | None = None, *, root: Path = REPO, chat=None) -> int:
    ap = argparse.ArgumentParser(description="每週學習迴圈")
    ap.add_argument("--days", type=int, default=7, help="往前看幾天（預設 7）")
    ap.add_argument("--dry-run", action="store_true", help="只印，不寫檔、不跑測試、不寄信")
    args = ap.parse_args(argv)
    try:
        run(args.days, dry_run=args.dry_run, root=root, chat=chat)
    except Exception as exc:                       # noqa: BLE001
        print(f"[WeeklyLearning] ⚠️ 未預期的錯誤：{str(exc)[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
