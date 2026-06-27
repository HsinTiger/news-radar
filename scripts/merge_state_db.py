#!/usr/bin/env python3
"""Phase 0：state branch 互踩防護——persist 前把 remote 多出來的列併進本地 DB。

問題：各 job（full_pipeline / submit-source / Mac compose）都 orphan force-push 整個
DB 檔到 state branch，並行寫入互相整碗覆蓋——曾兩次把手動投稿（user_submission）洗掉。

最小修法：persist 前先 `git show origin/state:...db` 拿到 remote 最新 DB，把「remote 有、
本地沒有」的列（以 id / PK 補）併進本地 DB 再 push。這樣即使有並行 job 在本 run 的
checkout 之後 push 了新列，也不會被本 run 的 force-push 洗掉（殘留視窗從數分鐘縮到數秒）。

鐵律 fail-safe：任何步驟出錯都 exit 0、不阻斷 persist（沒 remote / schema 不符 / 檔案
壞 → 就用本地 DB 照推，回到舊行為，絕不因為這支腳本讓 state 存不了）。

Usage: merge_state_db.py <local_db> <remote_state_db>
"""
import sqlite3
import sys

# 併哪些表（以各自 PK INSERT OR IGNORE）：news_items 最關鍵（手動投稿），drafts 次之。
_TABLES = ("news_items", "drafts")


def _merge_table(conn: sqlite3.Connection, table: str) -> int:
    # 確認兩邊都有這張表
    for db in ("main", "remote"):
        ok = conn.execute(
            f"SELECT 1 FROM {db}.sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not ok:
            return 0
    cur = conn.execute(
        f"INSERT OR IGNORE INTO main.{table} SELECT * FROM remote.{table}"
    )
    return cur.rowcount or 0


def merge(local_db: str, remote_db: str) -> int:
    conn = sqlite3.connect(local_db)
    total = 0
    try:
        conn.execute("ATTACH DATABASE ? AS remote", (remote_db,))
        for t in _TABLES:
            try:
                n = _merge_table(conn, t)
                if n:
                    print(f"[merge] {t}: 併入 {n} 筆 remote-only 列")
                total += n
            except Exception as e:  # noqa: BLE001 — 單表失敗不影響其他表/persist
                print(f"[merge] ⚠️ {t} 併入失敗（略過）：{e}")
        conn.commit()
    finally:
        conn.close()
    print(f"[merge] 完成，共併入 {total} 筆。")
    return total


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: merge_state_db.py <local_db> <remote_state_db>")
        sys.exit(0)  # fail-safe：參數不對也不阻斷 persist
    try:
        merge(sys.argv[1], sys.argv[2])
    except Exception as e:  # noqa: BLE001
        print(f"[merge] ⚠️ 失敗，用本地 DB 照推（不阻斷 persist）：{e}")
    sys.exit(0)
