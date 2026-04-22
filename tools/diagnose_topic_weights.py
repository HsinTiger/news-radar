"""
News Radar · Topic Weight Health Report（Phase 8.20 follow-up）
==============================================================

唯讀掃 `topic_weights` + `topic_weight_history` + `reflection_events` 三表，
回答 Topic 2 健檢三問：

  1. 有沒有權重被 back-prop 拉到離譜極端（逼近 0.30 地板或 2.00 天花板）？
  2. 有沒有類別從未被 back-prop 更新過（→ 樣本長期不足，η 被跳過）？
  3. 有沒有 update_reason 出現非預期值（合法集合：initial_seed / back_prop / manual）？

**唯讀**：不 UPDATE、不 INSERT、不 DELETE，完全安全。
**無 token 成本**：純 SQLite + stdlib。

用法：
    python tools/diagnose_topic_weights.py

    # 自訂離譜極端門檻（預設：floor 0.35 / ceil 1.95）
    python tools/diagnose_topic_weights.py --floor-threshold 0.40 --ceil-threshold 1.80

    # 指定輸出位置（預設：docs/research_briefs/topic2_reflector_health_<date>.md）
    python tools/diagnose_topic_weights.py --out /tmp/topic2_health.md

—— 2026-04-22 Hsin's Topic 2 (c) minimum viable plan
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# ---- 合法 update_reason（對齊 src/db.py + src/reflector_topic.py）----
VALID_UPDATE_REASONS = {"initial_seed", "back_prop", "manual"}

# ---- Clamp bounds（對齊 src/reflector_topic.py 常數）----
GLOBAL_FLOOR = 0.30
GLOBAL_CEIL = 2.00

DEFAULT_DB = "data/01_harvest/news_radar.db"
DEFAULT_OUT_DIR = "docs/research_briefs"


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _fmt_row(r: sqlite3.Row) -> dict:
    return {k: r[k] for k in r.keys()}


def build_report(
    conn: sqlite3.Connection,
    floor_threshold: float,
    ceil_threshold: float,
) -> str:
    """純組字串；連線由呼叫端管理。"""
    lines: List[str] = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines.append(f"# Topic 2 · Reflector Health Check · {today}")
    lines.append("")
    lines.append(f"**模式**: 唯讀（read-only），無任何 DB 寫入")
    lines.append(f"**離譜門檻**: 權重 ≤ {floor_threshold:.2f} 視為近地板；權重 ≥ {ceil_threshold:.2f} 視為近天花板")
    lines.append(f"**合法 update_reason**: {sorted(VALID_UPDATE_REASONS)}")
    lines.append("")

    # --- 1. 全景快照 ---
    lines.append("## 1. 全景快照")
    lines.append("")
    lines.append("| category_id | display_name | weight | update_reason | last_updated_at | sample_count | last_delta |")
    lines.append("|---|---|---:|---|---|---:|---:|")
    snapshot = conn.execute(
        "SELECT category_id, display_name, weight, update_reason, "
        "last_updated_at, sample_count, last_delta "
        "FROM topic_weights ORDER BY category_id"
    ).fetchall()
    for r in snapshot:
        last_delta = r["last_delta"]
        last_delta_s = f"{last_delta:+.3f}" if last_delta is not None else "—"
        lines.append(
            f"| `{r['category_id']}` "
            f"| {r['display_name']} "
            f"| {r['weight']:.3f} "
            f"| {r['update_reason']} "
            f"| {r['last_updated_at']} "
            f"| {r['sample_count']} "
            f"| {last_delta_s} |"
        )
    lines.append("")
    total_cats = len(snapshot)
    lines.append(f"**總類別數**: {total_cats}")
    lines.append("")

    # --- 2. 健檢三問 ---
    lines.append("## 2. 健檢三問")
    lines.append("")

    # Q1 極端權重
    near_floor = [r for r in snapshot if r["weight"] <= floor_threshold]
    near_ceil = [r for r in snapshot if r["weight"] >= ceil_threshold]
    lines.append("### Q1. 有沒有權重被拉到離譜極端？")
    lines.append("")
    if not near_floor and not near_ceil:
        lines.append(f"✅ 無。所有權重落在 ({floor_threshold:.2f}, {ceil_threshold:.2f}) 之間。")
    else:
        if near_floor:
            lines.append(f"⚠️  近地板（≤ {floor_threshold:.2f}，GLOBAL_FLOOR={GLOBAL_FLOOR}）：")
            for r in near_floor:
                lines.append(f"  - `{r['category_id']}` = {r['weight']:.3f}（reason={r['update_reason']}）")
        if near_ceil:
            lines.append(f"⚠️  近天花板（≥ {ceil_threshold:.2f}，GLOBAL_CEIL={GLOBAL_CEIL}）：")
            for r in near_ceil:
                lines.append(f"  - `{r['category_id']}` = {r['weight']:.3f}（reason={r['update_reason']}）")
    lines.append("")

    # Q2 從未被 back-prop 觸及的類別
    lines.append("### Q2. 有沒有類別從未被 back-prop 更新過？")
    lines.append("")
    # 定義：update_reason 仍為 initial_seed，代表冷啟動到現在都沒被 reflector 動過
    never_backprop = [r for r in snapshot if r["update_reason"] == "initial_seed"]
    if not never_backprop:
        lines.append("✅ 所有類別至少被 reflector 或 manual 動過一次。")
    else:
        lines.append(f"ℹ️  {len(never_backprop)}/{total_cats} 個類別 update_reason 仍為 `initial_seed`：")
        for r in never_backprop:
            lines.append(f"  - `{r['category_id']}`（samples={r['sample_count']}）")
        lines.append("")
        lines.append("**判讀**：若 reflector 跑過至少一輪但這些類別仍無變動，")
        lines.append("代表該類別跨平台樣本合計 < 5（MIN_SAMPLES_TOTAL），")
        lines.append("或連續多輪三平台樣本都 < 3（MIN_SAMPLES_PER_PLATFORM）。")
        lines.append("考慮：拓寬該類關鍵字 / 合併入其他類別 / 該類原本就不活躍（正常現象）。")
    lines.append("")

    # Q3 非預期 update_reason
    lines.append("### Q3. 有沒有 update_reason 非預期值？")
    lines.append("")
    illegal = [r for r in snapshot if r["update_reason"] not in VALID_UPDATE_REASONS]
    if not illegal:
        lines.append(f"✅ 所有 update_reason 都在合法集合 {sorted(VALID_UPDATE_REASONS)} 內。")
    else:
        lines.append(f"🚨 發現 {len(illegal)} 筆 update_reason 非預期值：")
        for r in illegal:
            lines.append(f"  - `{r['category_id']}` = `{r['update_reason']}`（⚠️ 不在合法集合）")
        lines.append("")
        lines.append("**行動建議**：檢查是否有外部腳本直接寫 topic_weights，或 reflector/seeder 邏輯被改過。")
    lines.append("")

    # --- 3. 近期 back-prop 活動 ---
    lines.append("## 3. 近期 back-prop 活動（topic_weight_history 最近 20 筆）")
    lines.append("")
    try:
        history = conn.execute(
            "SELECT category_id, recorded_at, weight_before, weight_after, "
            "delta, samples_in_window, rationale "
            "FROM topic_weight_history "
            "ORDER BY recorded_at DESC LIMIT 20"
        ).fetchall()
    except sqlite3.OperationalError as e:
        history = []
        lines.append(f"⚠️  無法讀取 topic_weight_history：{e}")
        lines.append("")

    if history:
        lines.append("| recorded_at | category_id | before → after | Δ | samples | rationale |")
        lines.append("|---|---|---:|---:|---:|---|")
        for r in history:
            delta = r["delta"]
            delta_s = f"{delta:+.3f}" if delta is not None else "—"
            wb = r["weight_before"] or 0.0
            wa = r["weight_after"] or 0.0
            lines.append(
                f"| {r['recorded_at']} "
                f"| `{r['category_id']}` "
                f"| {wb:.3f} → {wa:.3f} "
                f"| {delta_s} "
                f"| {r['samples_in_window']} "
                f"| {r['rationale']} |"
            )
    else:
        lines.append("（history 空或表不存在——代表 reflector 從未跑過，或 DB schema 較舊）")
    lines.append("")

    # --- 4. reflection_events 最近三筆 ---
    lines.append("## 4. reflection_events 最近三筆")
    lines.append("")
    try:
        events = conn.execute(
            "SELECT ran_at, status, samples_used, rationale "
            "FROM reflection_events "
            "ORDER BY ran_at DESC LIMIT 3"
        ).fetchall()
    except sqlite3.OperationalError as e:
        events = []
        lines.append(f"⚠️  無法讀取 reflection_events：{e}")
        lines.append("")

    if events:
        lines.append("| ran_at | status | samples_used | rationale |")
        lines.append("|---|---|---:|---|")
        for r in events:
            lines.append(
                f"| {r['ran_at']} "
                f"| {r['status']} "
                f"| {r['samples_used']} "
                f"| {r['rationale']} |"
            )
    else:
        lines.append("（空——reflector cron 可能從未觸發，或表不存在）")
    lines.append("")

    # --- 5. Verdict ---
    lines.append("## 5. Verdict")
    lines.append("")
    issues = []
    if near_floor:
        issues.append(f"{len(near_floor)} 類近地板")
    if near_ceil:
        issues.append(f"{len(near_ceil)} 類近天花板")
    if illegal:
        issues.append(f"{len(illegal)} 類 update_reason 非法")
    if issues:
        lines.append(f"⚠️  發現需人工覆核項：{', '.join(issues)}。")
        lines.append("**不立即動手**——先看趨勢（下週再跑一次比對），確認是真問題再處理。")
    else:
        lines.append("✅ **本週 reflector 行為正常，Topic 2 closed**。")
        lines.append("")
        lines.append("無離譜權重、無非法 update_reason。若有大量 `initial_seed` 類別，")
        lines.append("是 reflector guard rails（樣本不足保護）正常運作的結果，非 bug。")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("_自動產出 by `tools/diagnose_topic_weights.py`（唯讀）_")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite 路徑（預設 {DEFAULT_DB}）")
    parser.add_argument("--floor-threshold", type=float, default=0.35,
                        help="近地板門檻（預設 0.35）")
    parser.add_argument("--ceil-threshold", type=float, default=1.95,
                        help="近天花板門檻（預設 1.95）")
    parser.add_argument("--out", default=None, help="輸出 Markdown 路徑（預設 docs/research_briefs/topic2_reflector_health_<date>.md）")
    parser.add_argument("--print", action="store_true", help="同時印到 stdout")
    args = parser.parse_args(argv)

    db_path = Path(args.db).resolve()
    if not db_path.exists():
        print(f"[ERR] DB not found: {db_path}", file=sys.stderr)
        return 2

    conn = _connect(db_path)
    try:
        md = build_report(
            conn,
            floor_threshold=args.floor_threshold,
            ceil_threshold=args.ceil_threshold,
        )
    finally:
        conn.close()

    if args.out:
        out_path = Path(args.out)
    else:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out_path = Path(DEFAULT_OUT_DIR) / f"topic2_reflector_health_{today}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"[OK] report: {out_path}", file=sys.stderr)
    if args.print:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
