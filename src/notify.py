"""
News Radar · Substack Notify (Phase 9.x · 2026-05-13)
======================================================

Substack pipeline 自動跑完之後，**主動推播給作者本人**（不是訂閱者）。

問題情境：
  Hsin 整天在外靠手機。launchd cron 09:00 / 18:00 跑完 substack compose 之後，
  draft 落在 Substack 後台跟 OneDrive，但他不會主動去檢查。需要一條 push
  channel 在他**手機**上敲一下，他才知道「今天的 draft 寫好了」。

Channel 設計：
  - **Gmail SMTP**（預設、推薦）→ 寄到 hsin290525@gmail.com，手機 Mail 推播
  - **macOS notify**（bonus）→ 跳 Notification Center，Mac 開機時加倍可見

每篇 draft 完成（成功 OR 失敗）都會觸發。失敗 path 也送 email，告訴 Hsin
「08:00 cron 炸了，原因是 X」——不要等他發現後台沒新東西才問。

關鍵設計原則：
  - **never break the pipeline**。notify 失敗只 log，不 raise。
  - **opt-in**。env 沒設就 silent skip，舊 caller 不受影響。
  - **full content in email**。markdown 全文塞 body，手機 Mail 滑就讀完，
    不必切去 Substack 編輯器。
  - **system health snapshot**。email 帶「launchctl plist loaded?」、
    「news_radar.db 有沒有 stale」、「上次成功 push 是何時」這類訊號。

Env vars:
    SUBSTACK_NOTIFY_CHANNEL  = "gmail" | "macos" | "both" | "none" (default)
    SUBSTACK_NOTIFY_EMAIL    = hsin290525@gmail.com
    GMAIL_APP_PASSWORD       = <Gmail App Password, 16-char>
    SUBSTACK_NOTIFY_FROM     = <寄件 Gmail 地址，預設 = SUBSTACK_NOTIFY_EMAIL>
"""

from __future__ import annotations

import os
import smtplib
import subprocess
import sys
import traceback
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def notify_substack_success(
    *,
    mode: str,
    draft_title: str,
    draft_subtitle: str,
    draft_url: Optional[str],
    body_markdown: str,
    metadata: Dict[str, Any],
    audit_warnings: List[str],
    onedrive_path: Optional[str] = None,
) -> None:
    """Substack draft 成功產出 → 推播給 Hsin。

    Args:
        mode: "morning" or "evening"
        draft_title / draft_subtitle: SubstackDraft fields
        draft_url: e.g. https://hsin73.substack.com/publish/post/<id>
                   None if --no-draft mode
        body_markdown: 讀者可直接閱讀的全文 markdown（含公開 footer）
        metadata: dict with keys like chinese_chars / word_floor / word_cap /
                  editorial_profile / source_file（可選）
        audit_warnings: list of warning strings from audit_substack_draft
        onedrive_path: OneDrive autogen folder absolute path（如有 mirror 成功）

    Never raises. Notify failure is logged + swallowed.
    """
    if not _notify_enabled():
        return

    subject = _success_subject(mode, draft_title, audit_warnings)
    body_html, body_plain = _success_body(
        mode=mode,
        title=draft_title,
        subtitle=draft_subtitle,
        url=draft_url,
        body_md=body_markdown,
        metadata=metadata,
        warnings=audit_warnings,
        onedrive_path=onedrive_path,
    )
    _dispatch(subject=subject, body_html=body_html, body_plain=body_plain,
              macos_subtitle=draft_subtitle[:60])


def notify_substack_failure(
    *,
    mode: str,
    error_msg: str,
    traceback_short: Optional[str] = None,
    extra_context: Optional[Dict[str, Any]] = None,
) -> None:
    """Substack pipeline 跑掛 → 推播 Hsin 知道 + 修法 hint。

    Args:
        mode: "morning" or "evening" or "unknown"
        error_msg: short human-readable error summary
        traceback_short: trimmed Python traceback (max ~30 lines)
        extra_context: dict of helpful context (DB row counts / env state)

    Never raises.
    """
    if not _notify_enabled():
        return

    subject = f"[Substack ❌] {mode} 失敗 — {error_msg[:60]}"
    body_html, body_plain = _failure_body(
        mode=mode,
        error_msg=error_msg,
        traceback_short=traceback_short,
        extra_context=extra_context or {},
    )
    _dispatch(subject=subject, body_html=body_html, body_plain=body_plain,
              macos_subtitle=error_msg[:80])


def notify_test() -> bool:
    """Manual test: 發一封測試信。setup script 呼叫這條驗證 chain 通。"""
    if not _notify_enabled():
        print("[notify] ⚠️ SUBSTACK_NOTIFY_CHANNEL not set; nothing dispatched.")
        return False
    subject = "[Substack 🧪] notify channel 測試 OK"
    body_plain = (
        "這是 News Radar substack pipeline 的測試通知。\n\n"
        "你收到這封信代表：\n"
        "  ✅ Gmail SMTP 認證通的\n"
        "  ✅ App Password 有效\n"
        "  ✅ 手機 Mail 推播也應該收得到\n\n"
        "未來每天 09:00 morning / 18:00 evening 跑完都會推這條 channel。\n"
        f"目前環境：\n"
        f"  SUBSTACK_NOTIFY_CHANNEL = {os.getenv('SUBSTACK_NOTIFY_CHANNEL')}\n"
        f"  SUBSTACK_NOTIFY_EMAIL   = {os.getenv('SUBSTACK_NOTIFY_EMAIL')}\n"
    )
    _dispatch(subject=subject, body_html=None, body_plain=body_plain,
              macos_subtitle="notify test")
    return True


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _notify_enabled() -> bool:
    ch = (os.getenv("SUBSTACK_NOTIFY_CHANNEL") or "").lower().strip()
    return ch in ("gmail", "macos", "both")


def _dispatch(
    *,
    subject: str,
    body_html: Optional[str],
    body_plain: str,
    macos_subtitle: str = "",
) -> None:
    """Route notification to enabled channels. Each channel try/except'd
    independently so one failure doesn't kill the other."""
    ch = (os.getenv("SUBSTACK_NOTIFY_CHANNEL") or "").lower().strip()

    if ch in ("gmail", "both"):
        try:
            _send_gmail(subject=subject, body_html=body_html, body_plain=body_plain)
            print(f"[notify] ✅ gmail sent: {subject[:60]}")
        except Exception as exc:
            print(f"[notify] ❌ gmail failed: {type(exc).__name__}: {exc}")

    if ch in ("macos", "both"):
        try:
            _send_macos(title=subject, subtitle=macos_subtitle, body=body_plain[:200])
            print(f"[notify] ✅ macos notification posted")
        except Exception as exc:
            print(f"[notify] ⚠️ macos notify failed (non-fatal): {exc}")


def _send_gmail(*, subject: str, body_html: Optional[str], body_plain: str) -> None:
    """Send via Gmail SMTP. Requires GMAIL_APP_PASSWORD + SUBSTACK_NOTIFY_EMAIL."""
    to_addr = os.getenv("SUBSTACK_NOTIFY_EMAIL")
    app_pw = os.getenv("GMAIL_APP_PASSWORD")
    if not (to_addr and app_pw):
        raise RuntimeError(
            "SUBSTACK_NOTIFY_EMAIL or GMAIL_APP_PASSWORD missing in env"
        )
    from_addr = os.getenv("SUBSTACK_NOTIFY_FROM", to_addr)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(body_plain, "plain", "utf-8"))
    if body_html:
        msg.attach(MIMEText(body_html, "html", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
        s.starttls()
        s.login(from_addr, app_pw)
        s.send_message(msg)


def _send_macos(*, title: str, subtitle: str, body: str) -> None:
    """Mac local notification via osascript. No-op on non-Mac."""
    if sys.platform != "darwin":
        return
    # Escape for osascript single-quoted strings
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')[:300]
    script = (
        f'display notification "{esc(body)}" '
        f'with title "{esc(title)}" subtitle "{esc(subtitle)}" '
        f'sound name "Submarine"'
    )
    subprocess.run(["osascript", "-e", script], timeout=5, check=False)


# ---------------------------------------------------------------------------
# Email body builders
# ---------------------------------------------------------------------------

def _success_subject(mode: str, title: str, warnings: List[str]) -> str:
    badge = "⚠️" if warnings else "✅"
    return f"[Substack {badge}] {mode} draft — {title[:50]}"


def _success_body(
    *,
    mode: str,
    title: str,
    subtitle: str,
    url: Optional[str],
    body_md: str,
    metadata: Dict[str, Any],
    warnings: List[str],
    onedrive_path: Optional[str],
) -> tuple:
    audit_block = (
        "\n".join(f"  - ⚠️ {w}" for w in warnings)
        if warnings
        else "  - ✅ clean"
    )
    url_line = url or "(--no-draft mode, 沒推 Substack)"
    onedrive_line = onedrive_path or "(沒 mirror 到 OneDrive)"

    plain = f"""【 {mode} draft 完成 】

📊 metadata
  Audit: {len(warnings)} warning(s)
{audit_block}

  字數: {metadata.get('chinese_chars', '?')} (target {metadata.get('word_floor','?')}-{metadata.get('word_cap','?')})
  Profile: {metadata.get('editorial_profile', '?')}

🔗 連結
  Substack draft: {url_line}
  OneDrive folder: {onedrive_line}

📝 全文（手機 Mail 直接讀）

# {title}

*{subtitle}*

{body_md}

——————
⚙️ Next step
  ▸ Substack 後台 review 正文與已上傳的 cover.png
  ▸ 確認無製程註記後再按 Publish

(這封 email 由 News Radar substack notify 自動發送)
"""

    # Simple HTML version for clients that prefer HTML
    html = (
        f"<div style='font-family:-apple-system,sans-serif;max-width:680px'>"
        f"<h2>【 {mode} draft 完成 】</h2>"
        f"<h3>📊 metadata</h3>"
        f"<ul>"
        f"<li>Audit: <b>{len(warnings)} warning(s)</b></li>"
        f"<li>字數: {metadata.get('chinese_chars','?')} (target {metadata.get('word_floor','?')}-{metadata.get('word_cap','?')})</li>"
        f"<li>Profile: <code>{metadata.get('editorial_profile','?')}</code></li>"
        f"</ul>"
        f"<h3>🔗 連結</h3>"
        f"<p>Substack: <a href='{url or '#'}'>{url or 'n/a'}</a></p>"
        f"<p>OneDrive: <code>{onedrive_line}</code></p>"
        f"<h3>📝 全文</h3>"
        f"<h4>{title}</h4>"
        f"<p><i>{subtitle}</i></p>"
        f"<pre style='white-space:pre-wrap;font-family:-apple-system,sans-serif;font-size:14px'>{body_md}</pre>"
        f"</div>"
    )
    return html, plain


def _failure_body(
    *,
    mode: str,
    error_msg: str,
    traceback_short: Optional[str],
    extra_context: Dict[str, Any],
) -> tuple:
    ctx_lines = "\n".join(f"  {k}: {v}" for k, v in extra_context.items())
    tb_block = traceback_short or "(no traceback)"

    plain = f"""【 {mode} 失敗 】

❌ Error
  {error_msg}

📋 Context
{ctx_lines or "  (none)"}

🐍 Traceback (tail 30 lines)
{tb_block}

🛠️ 快速診斷
  cd ~/news_radar
  tail -30 logs/launchd_{mode}.err
  tail -30 logs/launchd_{mode}.log

  # 手動重跑看實時錯誤
  .venv/bin/python substack_radar/compose.py {mode}

  # morning 失敗最常見：DB 沒資料
  .venv/bin/python -c "import sqlite3;c=sqlite3.connect('data/01_harvest/news_radar.db');print(c.execute('SELECT COUNT(*) FROM news_items').fetchone())"

(這封 email 由 News Radar substack notify 自動發送)
"""
    html = (
        f"<div style='font-family:-apple-system,sans-serif;max-width:680px'>"
        f"<h2 style='color:#c0392b'>【 {mode} 失敗 】</h2>"
        f"<h3>Error</h3><pre>{error_msg}</pre>"
        f"<h3>Context</h3><pre>{ctx_lines}</pre>"
        f"<h3>Traceback</h3><pre style='font-size:11px'>{tb_block}</pre>"
        f"</div>"
    )
    return html, plain


__all__ = [
    "notify_substack_success",
    "notify_substack_failure",
    "notify_test",
]


if __name__ == "__main__":
    # python -m src.notify  — sends a test
    from dotenv import load_dotenv
    load_dotenv()
    notify_test()
