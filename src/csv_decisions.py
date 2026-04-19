"""
News Radar · CSV-Decisions 模組（Milestone 3）
功能：
1. 讀 drafts_for_review.csv，吃「decision」與可選「note」欄。
2. 若 CSV 原本沒有這兩欄，自動補空欄位並寫回（人類下次打開就能勾）。
3. 回傳 decision 清單 + 對應 draft 的 AI 原文。

接受的 decision 值（大小寫不拘）：
  - approve / ok / yes / star / 👍  → 當「好樣本」
  - reject / no / skip / drop / ❌   → 當「壞樣本」
  - （空白）                         → 忽略

與 DB 的錨點用 generated_at；沒有 generated_at 欄位就退而求其次用 title 完整匹配。
"""
from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

CSV_PATH = Path(__file__).resolve().parent.parent / "drafts_for_review.csv"

POSITIVE_TOKENS = {"approve", "ok", "yes", "star", "👍", "pass", "good"}
NEGATIVE_TOKENS = {"reject", "no", "skip", "drop", "❌", "bad"}


@dataclass
class CsvDecision:
    draft_id: Optional[str]
    title: str
    generated_at: Optional[str]
    decision: str               # "positive" / "negative"
    note: str
    ai_version: Optional[str]   # 從 DB 撈出的 full_text


def _classify(raw: str) -> Optional[str]:
    s = (raw or "").strip().lower()
    if not s:
        return None
    if s in POSITIVE_TOKENS:
        return "positive"
    if s in NEGATIVE_TOKENS:
        return "negative"
    # 寬鬆匹配：包含關鍵字就算
    for t in POSITIVE_TOKENS:
        if t in s:
            return "positive"
    for t in NEGATIVE_TOKENS:
        if t in s:
            return "negative"
    return None


def _ensure_decision_columns(csv_path: Path) -> List[dict]:
    """確保 CSV 有 decision / note 欄位；沒有就補空欄寫回。回傳現行的 rows。"""
    if not csv_path.exists():
        print(f"[CSV] 檔案不存在: {csv_path}")
        return []

    # 以 utf-8-sig 讀，因為 export_drafts.py 是用 utf-8-sig 寫的（BOM）
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    changed = False
    if "decision" not in fieldnames:
        fieldnames.append("decision")
        changed = True
    if "note" not in fieldnames:
        fieldnames.append("note")
        changed = True

    if changed:
        # 補齊每一列的空欄位，然後寫回
        for r in rows:
            r.setdefault("decision", "")
            r.setdefault("note", "")
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"[CSV] 已自動補 decision/note 欄位到 {csv_path.name}")
    return rows


def _lookup_draft(conn: sqlite3.Connection, generated_at: Optional[str], title: Optional[str]):
    if generated_at:
        row = conn.execute(
            "SELECT id, full_text, generated_at, title FROM drafts WHERE generated_at = ?",
            (generated_at,),
        ).fetchone()
        if row:
            return row
    if title:
        row = conn.execute(
            "SELECT id, full_text, generated_at, title FROM drafts WHERE title = ? ORDER BY generated_at DESC LIMIT 1",
            (title,),
        ).fetchone()
        if row:
            return row
    return None


def read_decisions(conn: sqlite3.Connection, csv_path: Path = CSV_PATH) -> List[CsvDecision]:
    rows = _ensure_decision_columns(csv_path)
    if not rows:
        return []

    decisions: List[CsvDecision] = []
    for r in rows:
        cls = _classify(r.get("decision", ""))
        if not cls:
            continue  # 沒勾就跳過
        draft_row = _lookup_draft(conn, r.get("generated_at"), r.get("title"))
        decisions.append(CsvDecision(
            draft_id=(draft_row["id"] if draft_row else None),
            title=r.get("title", "").strip(),
            generated_at=r.get("generated_at"),
            decision=cls,
            note=(r.get("note") or "").strip(),
            ai_version=(draft_row["full_text"] if draft_row else r.get("full_text")),
        ))
    print(f"[CSV] 讀到 {len(decisions)} 筆有效 decision")
    return decisions


if __name__ == "__main__":
    conn = sqlite3.connect(str(Path(__file__).resolve().parent.parent / "db" / "news_radar.db"))
    conn.row_factory = sqlite3.Row
    ds = read_decisions(conn)
    for d in ds:
        print(f"  [{d.decision}] {d.title[:40]}  note={d.note!r}  draft_id={d.draft_id}")
    conn.close()
