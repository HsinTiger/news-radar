from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone

from scripts import verify_remote_publish as verifier


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE publish_log(
          id INTEGER PRIMARY KEY,draft_id TEXT,platform TEXT,
          platform_post_id TEXT,posted_at TEXT,success INTEGER
        )
        """
    )
    return conn


def _seed(conn: sqlite3.Connection, platform: str, post_id: str | None) -> None:
    conn.execute(
        """INSERT INTO publish_log(
             draft_id,platform,platform_post_id,posted_at,success
           ) VALUES('draft',?,?,?,1)""",
        (platform, post_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def test_remote_readback_requires_every_requested_platform() -> None:
    conn = _conn()
    _seed(conn, "threads", "thread-1")

    async def _ok(_client, post_id):
        assert post_id == "thread-1"
        return {"ok": True, "views": 0}

    result = asyncio.run(
        verifier.verify(
            30,
            {"threads", "facebook"},
            conn=conn,
            fetchers={"threads": _ok, "facebook": _ok},
        )
    )
    assert result == 1


def test_remote_readback_accepts_readable_zero_metrics() -> None:
    conn = _conn()
    _seed(conn, "threads", "thread-1")

    async def _zero(_client, post_id):
        assert post_id == "thread-1"
        return {"ok": True, "views": 0, "likes": 0}

    result = asyncio.run(
        verifier.verify(
            30,
            {"threads"},
            conn=conn,
            fetchers={"threads": _zero},
        )
    )
    assert result == 0


def test_remote_readback_rejects_api_failure() -> None:
    conn = _conn()
    _seed(conn, "instagram", "ig-1")

    async def _fail(_client, _post_id):
        return {"ok": False, "error": {"code": 190}}

    result = asyncio.run(
        verifier.verify(
            30,
            {"instagram"},
            conn=conn,
            fetchers={"instagram": _fail},
        )
    )
    assert result == 1
