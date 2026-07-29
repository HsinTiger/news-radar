#!/usr/bin/env python3
"""Keep the投稿控制台 honest without anyone having to check it by hand.

Why this exists
---------------
Two GitHub workflows carry the submission lifecycle, and BOTH are scheduled
crons that GitHub coalesces and deprioritises under load. Declared cadence vs.
observed cadence on 2026-07-29:

    submission-poller.yml    */5 * * * *      → actually 1-3 HOURS apart
    operational-sync.yml     17,47 * * * *    → actually 1-3 HOURS apart

That produces two different lies on the console, which look the same to a
human ("投稿了但沒在寫稿") but have opposite causes:

  1. poller late   → submission sits in `queued`; nothing has been written yet.
  2. sync late     → the draft ALREADY EXISTS on Substack, but the console
                     still says 「素材已入庫（尚未建立草稿）」 for hours.

The Cloudflare Worker now kicks the poller the instant a submission lands,
which covers (1) in the common case. Nothing covered (2), and nothing covered
(1) when that instant kick itself fails.

So both corrections hang off the one clock in this system that is actually
reliable: the Mac's launchd, firing `news_radar_substack_fast.sh` every 300s.
The Mac must be up for composition to happen at all, so this adds no new
dependency.

One GET against the control plane serves both checks.

Contract
--------
- The only mutations are workflow dispatches, which are idempotent: the poller
  claims at most one submission per run and no-ops on an empty queue; the sync
  recomputes state from canonical evidence.
- No new credential. Dispatching `operational-sync.yml` deliberately reuses the
  GitHub trust boundary instead of putting the control plane's SERVICE token on
  the Mac — the Mac holds only the owner (read) token, and it should stay that
  way.
- **Never fails the caller.** Always exits 0. This runs ahead of the drain and
  must not be able to stop drafts from being written.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

POLLER_WORKFLOW = "submission-poller.yml"
SYNC_WORKFLOW = "operational-sync.yml"
DEFAULT_REPO = "HsinTiger/news-radar"
# Same value as the SOCIAL_OPS_API_URL repo variable. Defaulted rather than
# required: the Mac's .env only carries the owner token, and a watchdog that
# silently no-ops because one env var is missing is worse than no watchdog.
DEFAULT_API_URL = "https://news-radar-submit.smartmmmoney.workers.dev"

# One launchd tick (5 min) past the worker's instant nudge. Anything older than
# this means the instant path did not land and the cron has not covered for it.
STUCK_AFTER_MINUTES = int(os.getenv("SUBMISSION_STUCK_MINUTES", "6"))
# Submissions the poller is supposed to claim. `claimed` is excluded: it has
# already been taken and has its own lease expiry inside the worker.
STUCK_STATUSES = {"queued"}
# Console states that mean "no Substack draft confirmed yet".
UNCONFIRMED_STATUSES = {
    "queued",
    "claimed",
    "dispatched",
    "source_queued",
    "processing",
}
CONTROL_SUBMISSION_PREFIX = "control_submission:"


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(REPO / ".env")


def _submissions() -> list[dict]:
    """Recent submissions as the console sees them. [] on any problem."""
    import httpx

    base = (os.getenv("SOCIAL_OPS_API_URL") or DEFAULT_API_URL).rstrip("/")
    token = os.getenv("SOCIAL_OPS_OWNER_TOKEN", "") or os.getenv(
        "SOCIAL_OPS_SERVICE_TOKEN", ""
    )
    if not token:
        print("[nudge] ℹ️ 無 SOCIAL_OPS_OWNER_TOKEN，略過。")
        return []
    try:
        response = httpx.get(
            f"{base}/api/submissions?limit=25",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
        response.raise_for_status()
        return (response.json() or {}).get("submissions") or []
    except Exception as exc:
        print(f"[nudge] ⚠️ 讀不到控制台投稿清單：{type(exc).__name__}: {exc}")
        return []


def _age_minutes(raw: str) -> int | None:
    try:
        created = datetime.fromisoformat(str(raw or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return int((datetime.now(timezone.utc) - created).total_seconds() // 60)


def _stuck_in_queue(rows: list[dict]) -> list[dict]:
    """Submissions the poller should have claimed by now."""
    out = []
    for row in rows:
        if row.get("status") not in STUCK_STATUSES:
            continue
        age = _age_minutes(row.get("created_at"))
        if age is not None and age >= STUCK_AFTER_MINUTES:
            out.append({**row, "_age_min": age})
    return out


def _drafted_locally() -> set[str]:
    """Control-plane submission ids whose Substack draft already exists here.

    Mirrors the derivation in sync_social_ops.build_submission_updates: a
    `user_substack` row carrying `control_submission:<id>` with both a remote
    draft id and a drafted-at stamp IS the evidence that the draft exists.
    """
    db = Path(os.getenv("NEWS_RADAR_DB") or (REPO / "data/01_harvest/news_radar.db"))
    if not db.exists():
        return set()
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return set()
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(news_items)")}
        if not {"substack_draft_id", "substack_drafted_at", "tags"} <= columns:
            return set()
        rows = conn.execute(
            """
            SELECT tags FROM news_items
             WHERE feed_name='user_substack'
               AND substack_draft_id IS NOT NULL
               AND substack_drafted_at IS NOT NULL
               AND tags LIKE '%control_submission:%'
            """
        ).fetchall()
    except sqlite3.Error:
        return set()
    finally:
        conn.close()

    ids: set[str] = set()
    for (tags,) in rows:
        import json

        try:
            values = json.loads(tags or "[]")
        except (TypeError, ValueError):
            continue
        for value in values:
            if isinstance(value, str) and value.startswith(CONTROL_SUBMISSION_PREFIX):
                ids.add(value[len(CONTROL_SUBMISSION_PREFIX):])
    return ids


def _unconfirmed_drafts(rows: list[dict]) -> list[dict]:
    """Drafts that exist locally but which the console has not caught up to."""
    drafted = _drafted_locally()
    if not drafted:
        return []
    return [
        {**row, "_age_min": _age_minutes(row.get("created_at")) or 0}
        for row in rows
        if row.get("id") in drafted and row.get("status") in UNCONFIRMED_STATUSES
    ]


def _dispatch(workflow: str) -> bool:
    """Fire a workflow. Reuses state_store's repo-owner-bound token resolution
    so a switched `gh` active account cannot silently break this the way it
    broke the state lease on 2026-07-29."""
    import httpx

    repo = os.getenv("GITHUB_REPOSITORY", DEFAULT_REPO)
    try:
        from state_store import _resolve_token

        token = _resolve_token(repo)
    except Exception as exc:
        print(f"[nudge] ⚠️ 取不到 GitHub token：{type(exc).__name__}: {exc}")
        return False
    try:
        response = httpx.post(
            f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"ref": "main"},
            timeout=30,
        )
    except Exception as exc:
        print(f"[nudge] ⚠️ dispatch {workflow} 失敗：{type(exc).__name__}: {exc}")
        return False
    if response.status_code != 204:
        print(
            f"[nudge] ⚠️ dispatch {workflow} 被拒 HTTP {response.status_code}: "
            f"{response.text[:200]}"
        )
        return False
    return True


def _label(row: dict) -> str:
    return (row.get("note") or row.get("target") or "?")[:40]


def main() -> int:
    _load_env()
    rows = _submissions()
    if not rows:
        return 0

    stuck = _stuck_in_queue(rows)
    if stuck:
        print(
            f"[nudge] ⏰ {len(stuck)} 筆投稿卡在 queued 超過 {STUCK_AFTER_MINUTES} 分鐘"
            f"（GitHub 排程 cron 又被延後）："
        )
        for row in stuck:
            print(f"  · {row.get('id','?')[:12]}  {row['_age_min']:>4} 分鐘  {_label(row)}")
        ok = _dispatch(POLLER_WORKFLOW)
        print(
            f"[nudge] {'✅ 已重新觸發' if ok else '❌ 重新觸發失敗（5 分鐘後再試）'}"
            f" {POLLER_WORKFLOW}。"
        )

    unconfirmed = _unconfirmed_drafts(rows)
    if unconfirmed:
        print(
            f"[nudge] 📣 {len(unconfirmed)} 筆草稿其實已經寫好了，控制台還沒更新："
        )
        for row in unconfirmed:
            print(
                f"  · {row.get('id','?')[:12]}  顯示 {row.get('status')}  {_label(row)}"
            )
        ok = _dispatch(SYNC_WORKFLOW)
        print(
            f"[nudge] {'✅ 已重新觸發' if ok else '❌ 重新觸發失敗（5 分鐘後再試）'}"
            f" {SYNC_WORKFLOW}。"
        )

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never break the drain that follows
        print(f"[nudge] ⚠️ 未預期錯誤，略過：{type(exc).__name__}: {exc}")
        sys.exit(0)
