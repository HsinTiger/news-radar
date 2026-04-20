"""
News Radar · Local Notify（Phase 8.20 附帶）
=============================================
薄薄一層 Mac 地端通知。Hsin 的要求：系統代班被攔下時，
要在 Mac 跳本地 notification 讓他知道（不必刪 DB，他手動處理）。

設計原則：
  - **平台偵測**：只在 darwin 上呼叫 osascript，其他 OS 一律 no-op 回 False。
  - **never throw**：通知失敗不影響主流程——publisher 的優先責任是 fail-safe。
  - **不 depend 業務邏輯**：不知道『draft / quality_guard / publisher』，
    只負責把 title + body 送進 macOS Notification Center。
  - **安全第一**：osascript 的 `display notification` 只認 quoted string，
    我們用 shlex 先 escape 所有輸入，避免『'; do evil; '」之類 injection。

用法：
    from src.local_notify import notify_quality_block
    ok = notify_quality_block(
        draft_id="abc123",
        reasons_one_line="templated_fallback_marker: 【系統代班速報】",
    )

—— 2026-04-21 overnight, Cowork Claude
"""
from __future__ import annotations

import shlex
import subprocess
import sys
from typing import Optional


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _run_osascript(script: str, timeout_sec: float = 3.0) -> bool:
    """osascript -e <script>；有 exception 一律吞掉回 False，從不 propagate。"""
    try:
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return False


def notify(
    title: str,
    body: str,
    subtitle: Optional[str] = None,
    sound: str = "Submarine",
) -> bool:
    """跳一條 macOS 本地通知。非 darwin 回 False 不做事。"""
    if not _is_macos():
        return False

    # osascript display notification "BODY" with title "TITLE" subtitle "SUBTITLE" sound name "NAME"
    # 所有欄位一律用 shlex.quote 把內容丟到 AppleScript 字串裡會太 over——
    # AppleScript 用自己的雙引號字面量，我們改用簡單但安全的作法：
    # 1) 把 " 換成 \" ；2) 把 \ 換成 \\ ；3) 把換行換成空白。
    def _esc(s: str) -> str:
        return (
            (s or "")
            .replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", " ")
            .replace("\r", " ")
        )[:240]  # Notification Center 超過也會被截，自己先截乾淨

    parts = [f'display notification "{_esc(body)}"', f'with title "{_esc(title)}"']
    if subtitle:
        parts.append(f'subtitle "{_esc(subtitle)}"')
    if sound:
        parts.append(f'sound name "{_esc(sound)}"')
    script = " ".join(parts)
    return _run_osascript(script)


def notify_quality_block(draft_id: str, reasons_one_line: str) -> bool:
    """Publisher 用：攔下系統代班時跳通知。
    return True 表示 osascript 至少有被執行過（不保證使用者真的看到）。
    """
    return notify(
        title="News Radar · 攔下代班假文",
        body=f"draft_id={draft_id[:16]}…\n{reasons_one_line}",
        subtitle="請到 DB 清理 queue_status='failed' 的這筆（或改回 queued 重試）",
    )


__all__ = ["notify", "notify_quality_block"]


if __name__ == "__main__":
    # 手動測試：python -m src.local_notify
    ok = notify_quality_block(
        draft_id="test1234567890abcdef",
        reasons_one_line="[block] templated_fallback_marker: 【系統代班速報】",
    )
    print(f"notify_quality_block returned: {ok}")
    print(f"(on non-macOS this will always be False, that's expected)")
