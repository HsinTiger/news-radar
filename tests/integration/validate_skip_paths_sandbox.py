"""
Sandbox validator for test_process_item_skip_paths.py
（沙箱無 pytest，用這個 script 跑同樣的 scenario、自己印結果）

跑法：
    cd news_radar && python tests/integration/validate_skip_paths_sandbox.py
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_BASE))

from src import db as dbmod  # noqa: E402
import run_pipeline  # noqa: E402


def _seed_fetched_news(conn, news_id="n_skip"):
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO news_items
        (id, feed_name, feed_tier, url, title, published_at, fetched_at,
         og_image_url, clean_markdown, word_count, status)
        VALUES (?, 'TestFeed', 'primary', ?, ?, ?, ?, ?, ?, 500, 'fetched')
        """,
        (
            news_id,
            f"https://example.com/{news_id}",
            f"Test article {news_id}",
            now_iso,
            now_iso,
            f"https://example.com/{news_id}/img.jpg",
            "Lorem ipsum " * 100,
        ),
    )
    conn.commit()
    return conn.execute("SELECT * FROM news_items WHERE id=?", (news_id,)).fetchone()


async def _return_none(*_a, **_kw):
    return None


async def _high_score(*_a, **_kw):
    from src.schema import NewsScore, ScoreBreakdown
    return NewsScore(
        confidence_score=0.95,
        editorial_note="test editorial hook",
        score_breakdown=ScoreBreakdown(
            data_density=0.9,
            strategic_signal=0.9,
            news_novelty=0.9,
            persona_fit=0.9,
        ),
        primary_topic_tag="test",
    )


async def _always_ok(*_a, **_kw):
    return True


def _prep_tmp_db():
    tmp = tempfile.mkdtemp(prefix="news_radar_test_")
    db_path = Path(tmp) / "test_radar.db"
    schema_src = _BASE / "data" / "01_harvest" / "schema.sql"
    dbmod.DB_PATH = db_path
    dbmod.SCHEMA_PATH = schema_src
    dbmod.init_db()
    return db_path


def _reset_tmp_db(db_path):
    # wipe + re-init
    if db_path.exists():
        db_path.unlink()
    dbmod.init_db()


def run():
    results = []

    # Case 1: scorer fail → skipped_no_llm
    db_path = _prep_tmp_db()
    orig_score = run_pipeline.score_news
    run_pipeline.score_news = _return_none
    try:
        with dbmod.get_conn() as conn:
            row = _seed_fetched_news(conn, "n_scorer_fail")
            result = asyncio.run(run_pipeline.process_item(conn, row))
            drafts = conn.execute(
                "SELECT COUNT(*) AS n FROM drafts WHERE news_id=?",
                ("n_scorer_fail",),
            ).fetchone()["n"]
            news_status = conn.execute(
                "SELECT status FROM news_items WHERE id=?", ("n_scorer_fail",)
            ).fetchone()["status"]

        ok = (
            result == "skipped_no_llm"
            and drafts == 0
            and news_status == "fetched"
        )
        results.append(
            ("scorer_fail_returns_skipped_no_llm",
             ok,
             f"result={result!r} drafts={drafts} status={news_status!r}")
        )
    finally:
        run_pipeline.score_news = orig_score

    # Case 2: composer fail → skipped_no_llm
    _reset_tmp_db(db_path)
    orig_score = run_pipeline.score_news
    orig_compose = run_pipeline.compose_multi_platform
    import src.image_manager as im
    orig_media = im.check_media_accessibility
    run_pipeline.score_news = _high_score
    run_pipeline.compose_multi_platform = _return_none
    im.check_media_accessibility = _always_ok
    try:
        with dbmod.get_conn() as conn:
            row = _seed_fetched_news(conn, "n_composer_fail")
            result = asyncio.run(run_pipeline.process_item(conn, row))
            drafts = conn.execute(
                "SELECT COUNT(*) AS n FROM drafts WHERE news_id=?",
                ("n_composer_fail",),
            ).fetchone()["n"]
            news_status = conn.execute(
                "SELECT status FROM news_items WHERE id=?", ("n_composer_fail",)
            ).fetchone()["status"]

        ok = (
            result == "skipped_no_llm"
            and drafts == 0
            and news_status == "fetched"
        )
        results.append(
            ("composer_fail_returns_skipped_no_llm",
             ok,
             f"result={result!r} drafts={drafts} status={news_status!r}")
        )
    finally:
        run_pipeline.score_news = orig_score
        run_pipeline.compose_multi_platform = orig_compose
        im.check_media_accessibility = orig_media

    # Case 3: regression guard — no fabricated confidence_score>=0.99 when scorer fails
    _reset_tmp_db(db_path)
    orig_score = run_pipeline.score_news
    run_pipeline.score_news = _return_none
    try:
        with dbmod.get_conn() as conn:
            row = _seed_fetched_news(conn, "n_regress")
            asyncio.run(run_pipeline.process_item(conn, row))
            fabricated = conn.execute(
                "SELECT COUNT(*) AS n FROM drafts WHERE confidence_score >= 0.99"
            ).fetchone()["n"]
        results.append(
            ("no_fabricated_confidence_when_scorer_fails",
             fabricated == 0,
             f"fabricated_rows={fabricated}")
        )
    finally:
        run_pipeline.score_news = orig_score

    print(f"\n{'=' * 60}")
    print("Skip-path sandbox validator results")
    print("=" * 60)
    passed = 0
    for name, ok, detail in results:
        marker = "✅" if ok else "❌"
        print(f"{marker} {name}: {detail}")
        if ok:
            passed += 1
    print("=" * 60)
    print(f"Passed: {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(run())
