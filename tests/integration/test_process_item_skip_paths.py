"""
News Radar · Integration test · process_item skip invariants (Phase 8.19)

Phase 8.19 移除了兩個 quality-crushing footguns：
  1. composer 回 None 時塞「緊急代班範本」到 queue（emergency template）
  2. scorer 回 None 時偽造 confidence_score=1.0 強推 auto-approve（暴力發布模式）

這個測試鎖定替代行為的合約：
  - scorer 雙路徑都失敗 → process_item 回 "skipped_no_llm"
  - composer 雙路徑都失敗 → process_item 回 "skipped_no_llm"
  - 任一種 skip 都不該建立 drafts / platform_drafts / publish_queue 列
  - 任一種 skip 都不該改 news_items.status（讓下一輪 cron Gemini 恢復後重試）

測試策略：monkeypatch `run_pipeline.score_news` / `run_pipeline.compose_multi_platform`
直接回 None，其餘流程不動，驗 DB 終態。
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone

import pytest

import run_pipeline
from src import db as dbmod
from src.schema import MultiPlatformDraft, PlatformVariant
from src.topic_classifier import TopicClassification


def _seed_fetched_news(
    conn: sqlite3.Connection, news_id: str = "n_skip"
) -> sqlite3.Row:
    """塞一筆 status='fetched'、內容夠長的新聞，回傳對應的 row。"""
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
    row = conn.execute(
        "SELECT * FROM news_items WHERE id = ?", (news_id,)
    ).fetchone()
    assert row is not None
    return row


async def _noop_score_news(*args, **kwargs):
    """模擬 Gemini → Claude CLI 都失敗：scorer 回 None。"""
    return None


async def _noop_compose(*args, **kwargs):
    """模擬 Gemini → Claude CLI 都失敗：composer 回 None。"""
    return None


async def _fake_make_passthrough_score(monkeypatch):
    """給 composer-skip 測試用：讓 scorer 回一個高分 NewsScore，流程才會走到 composer。"""
    from src.scorer import NewsScore, ScoreBreakdown

    async def _high_score(*_args, **_kwargs):
        return NewsScore(
            confidence_score=0.95,
            editorial_note="test editorial hook",
            score_breakdown=ScoreBreakdown(
                data_density=0.9,
                strategic_signal=0.9,
                news_novelty=0.9,
                persona_fit=0.9,
            ),
        )

    monkeypatch.setattr(run_pipeline, "score_news", _high_score)


def _bundle(body: str) -> MultiPlatformDraft:
    def variant(label: str) -> PlatformVariant:
        return PlatformVariant(
            title=f"{label} useful hook",
            body=body,
            hashtags=["#AI"],
            primary_topic_tag="#AI",
            char_count=len(body),
        )

    return MultiPlatformDraft(
        fb=variant("Facebook"),
        ig=variant("Instagram"),
        threads=variant("Threads"),
        image_url="https://images.example.net/cover.jpg",
    )


def _patch_composable_path(monkeypatch, bundles: list[MultiPlatformDraft]) -> list:
    calls: list = []

    async def _compose(*args, **kwargs):
        calls.append((args, kwargs))
        return bundles[min(len(calls) - 1, len(bundles) - 1)]

    async def _topic(*_args, **_kwargs):
        return TopicClassification("ai_application", 0.9, "test")

    async def _accessible(*_args, **_kwargs):
        return True

    monkeypatch.setattr(run_pipeline, "compose_multi_platform", _compose)
    monkeypatch.setattr(run_pipeline, "classify_topic", _topic)
    monkeypatch.setattr(run_pipeline.image_manager, "check_media_accessibility", _accessible)
    monkeypatch.setattr(run_pipeline, "save_md_draft", lambda *_args, **_kwargs: None)
    return calls


def test_scorer_fail_returns_skipped_no_llm(tmp_db, monkeypatch):
    """scorer 回 None 時：
       (a) process_item 回 "skipped_no_llm"
       (b) drafts / platform_drafts / publish_queue 都沒列
       (c) news_items.status 仍為 'fetched'（讓下一輪重試）
    """
    monkeypatch.setattr(run_pipeline, "score_news", _noop_score_news)

    with dbmod.get_conn() as conn:
        row = _seed_fetched_news(conn, "n_scorer_fail")

        result = asyncio.run(run_pipeline.process_item(conn, row))

        assert result == "skipped_no_llm"

        # 沒有 draft 被建立
        drafts = conn.execute(
            "SELECT COUNT(*) AS n FROM drafts WHERE news_id = ?",
            ("n_scorer_fail",),
        ).fetchone()
        assert drafts["n"] == 0

        # news 狀態保持 fetched（下一輪 Gemini 恢復後會重跑）
        news = conn.execute(
            "SELECT status, drop_reason FROM news_items WHERE id = ?",
            ("n_scorer_fail",),
        ).fetchone()
        assert news["status"] == "fetched"
        assert news["drop_reason"] is None

        # publish_queue 不該有對應列
        queue_count = conn.execute(
            """SELECT COUNT(*) AS n FROM drafts
               WHERE news_id = ? AND queue_status IS NOT NULL""",
            ("n_scorer_fail",),
        ).fetchone()
        assert queue_count["n"] == 0


def test_composer_fail_returns_skipped_no_llm(tmp_db, monkeypatch):
    """scorer 過關、composer 回 None 時：
       (a) process_item 回 "skipped_no_llm"
       (b) 沒有 draft 進 queue（queue_status 保持 NULL）
       (c) news_items.status 沒被標成 'dropped'
    """
    # 先讓 scorer 回高分，確保流程走到 composer
    asyncio.run(_fake_make_passthrough_score(monkeypatch))
    monkeypatch.setattr(run_pipeline, "compose_multi_platform", _noop_compose)

    # 避開 media gating 真的連外網
    import src.image_manager as im
    async def _always_ok(*_args, **_kwargs):
        return True
    monkeypatch.setattr(im, "check_media_accessibility", _always_ok)

    with dbmod.get_conn() as conn:
        row = _seed_fetched_news(conn, "n_composer_fail")

        result = asyncio.run(run_pipeline.process_item(conn, row))

        assert result == "skipped_no_llm"

        # 沒有 draft 被建立
        drafts = conn.execute(
            "SELECT COUNT(*) AS n FROM drafts WHERE news_id = ?",
            ("n_composer_fail",),
        ).fetchone()
        assert drafts["n"] == 0

        # 關鍵：沒有走「暴力發布」，status 不該是 dropped
        news = conn.execute(
            "SELECT status FROM news_items WHERE id = ?",
            ("n_composer_fail",),
        ).fetchone()
        assert news["status"] == "fetched"


def test_scorer_fail_does_not_fabricate_confidence(tmp_db, monkeypatch):
    """Regression guard：確保舊版『scorer-fail 塞 confidence_score=1.0 強推』沒復活。
    若未來有人重新加入 fallback 偽分數，這個測試會抓到。
    """
    monkeypatch.setattr(run_pipeline, "score_news", _noop_score_news)

    with dbmod.get_conn() as conn:
        row = _seed_fetched_news(conn, "n_regress")

        asyncio.run(run_pipeline.process_item(conn, row))

        # 任何 draft 紀錄都不該出現；舊 bug 會塞一筆 confidence_score=1.0
        fabricated = conn.execute(
            """SELECT COUNT(*) AS n FROM drafts
               WHERE confidence_score >= 0.99""",
        ).fetchone()
        assert fabricated["n"] == 0, (
            "偵測到 confidence_score >= 0.99 的 draft。"
            "Phase 8.19 已移除暴力發布偽造分數，若此測試 fail 表示 footgun 復活。"
        )


def test_rewrite_issue_gets_one_retry_then_queues_clean_result(tmp_db, monkeypatch):
    asyncio.run(_fake_make_passthrough_score(monkeypatch))
    calls = _patch_composable_path(
        monkeypatch,
        [
            _bundle("完整分析先放一個虛構連結 https://example.com，這段必須被重寫。"),
            _bundle("完整分析改成只保留來源能支持的判斷，並且清楚交代讀者能帶走什麼。"),
        ],
    )
    with dbmod.get_conn() as conn:
        row = _seed_fetched_news(conn, "n_rewrite_resolved")
        result = asyncio.run(run_pipeline.process_item(conn, row, compose_only=True))
        assert result == "queued"
        assert len(calls) == 2
        decisions = conn.execute(
            """SELECT attempt,decision,COUNT(*) AS n
                 FROM content_quality_evaluations
                GROUP BY attempt,decision ORDER BY attempt"""
        ).fetchall()
        assert [tuple(item) for item in decisions] == [
            (1, "rewrite", 3),
            (2, "pass", 3),
        ]
        draft = conn.execute(
            "SELECT status,queue_status FROM drafts WHERE news_id='n_rewrite_resolved'"
        ).fetchone()
        assert tuple(draft) == ("auto_approved", "queued")


def test_unresolved_rewrite_is_held_out_of_automatic_queue(tmp_db, monkeypatch):
    asyncio.run(_fake_make_passthrough_score(monkeypatch))
    bad = _bundle("完整分析仍引用虛構連結 https://example.com，所以不得進自動發布佇列。")
    calls = _patch_composable_path(monkeypatch, [bad, bad])
    with dbmod.get_conn() as conn:
        row = _seed_fetched_news(conn, "n_rewrite_unresolved")
        result = asyncio.run(run_pipeline.process_item(conn, row, compose_only=True))
        assert result == "drafted"
        assert len(calls) == 2
        draft = conn.execute(
            "SELECT status,queue_status FROM drafts WHERE news_id='n_rewrite_unresolved'"
        ).fetchone()
        assert tuple(draft) == ("pending_review", None)


def test_threads_only_scope_composes_only_threads_and_queues(tmp_db, monkeypatch):
    asyncio.run(_fake_make_passthrough_score(monkeypatch))
    calls = _patch_composable_path(
        monkeypatch,
        [_bundle("完整分析只需要產出 Threads 版本，並保留具體可驗證的判斷與限制。")],
    )
    with dbmod.get_conn() as conn:
        row = _seed_fetched_news(conn, "n_threads_scope")
        result = asyncio.run(
            run_pipeline.process_item(
                conn,
                row,
                compose_only=True,
                requested_platforms={"threads"},
            )
        )
        assert result == "queued"
        assert calls[0][1]["platforms"] == ["threads"]
        platforms = conn.execute(
            "SELECT platform FROM platform_drafts WHERE draft_id=(SELECT id FROM drafts WHERE news_id=?)",
            ("n_threads_scope",),
        ).fetchall()
        assert [item[0] for item in platforms] == ["threads"]


def test_missing_requested_variant_fails_closed_without_draft(tmp_db, monkeypatch):
    asyncio.run(_fake_make_passthrough_score(monkeypatch))
    fb_only = _bundle("Composer 意外只回傳 Facebook 版本。")
    fb_only.threads = None
    calls = _patch_composable_path(monkeypatch, [fb_only])
    with dbmod.get_conn() as conn:
        row = _seed_fetched_news(conn, "n_missing_threads")
        result = asyncio.run(
            run_pipeline.process_item(
                conn,
                row,
                compose_only=True,
                requested_platforms={"threads"},
            )
        )
        assert result == "skipped_no_llm"
        assert calls[0][1]["platforms"] == ["threads"]
        assert conn.execute(
            "SELECT COUNT(*) FROM drafts WHERE news_id='n_missing_threads'"
        ).fetchone()[0] == 0


def test_recovery_high_risk_claim_requires_readable_corroboration_brief(
    tmp_db, monkeypatch
):
    asyncio.run(_fake_make_passthrough_score(monkeypatch))
    calls = _patch_composable_path(
        monkeypatch,
        [_bundle("這段不應被撰寫，因為權威佐證本文沒有進入 brief。")],
    )
    monkeypatch.setenv("AUTOMATION_MODE", "recovery")

    import src.gather as gather

    monkeypatch.setattr(gather, "has_authoritative_corroboration", lambda *_a, **_k: True)
    monkeypatch.setattr(gather, "gather_brief", lambda *_a, **_k: "")

    with dbmod.get_conn() as conn:
        _seed_fetched_news(conn, "n_missing_corroboration_body")
        conn.execute(
            """UPDATE news_items
                  SET feed_name='某新聞網',feed_tier='secondary',
                      title='市長被控隱匿問題食品回收資料',
                      clean_markdown='食安事件涉及不合格食品與回收範圍。'
                WHERE id='n_missing_corroboration_body'"""
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM news_items WHERE id='n_missing_corroboration_body'"
        ).fetchone()

        result = asyncio.run(
            run_pipeline.process_item(
                conn,
                row,
                compose_only=True,
                requested_platforms={"threads"},
            )
        )

        assert result == "skipped_insufficient_evidence"
        assert calls == []
        assert conn.execute(
            "SELECT COUNT(*) FROM drafts WHERE news_id='n_missing_corroboration_body'"
        ).fetchone()[0] == 0


def test_submission_platform_tags_cannot_be_broadened_by_scheduler(tmp_db, monkeypatch):
    asyncio.run(_fake_make_passthrough_score(monkeypatch))
    calls = _patch_composable_path(
        monkeypatch,
        [_bundle("這份內容只允許 Facebook，不應在 Threads cycle 被偷偷擴張。")],
    )
    with dbmod.get_conn() as conn:
        row = _seed_fetched_news(conn, "n_fb_only")
        conn.execute(
            "UPDATE news_items SET tags='[\"platform:fb\"]' WHERE id='n_fb_only'"
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM news_items WHERE id='n_fb_only'"
        ).fetchone()
        result = asyncio.run(
            run_pipeline.process_item(
                conn,
                row,
                compose_only=True,
                requested_platforms={"threads"},
            )
        )
        assert result == "skipped_platform_scope"
        assert calls == []
        assert conn.execute(
            "SELECT COUNT(*) FROM drafts WHERE news_id='n_fb_only'"
        ).fetchone()[0] == 0


def test_substack_sources_are_never_visible_to_meta_pending_query(tmp_db):
    with dbmod.get_conn() as conn:
        _seed_fetched_news(conn, "n_meta_source")
        _seed_fetched_news(conn, "n_substack_source")
        conn.execute(
            """UPDATE news_items
                  SET feed_name='user_substack',tags='["substack_source"]'
                WHERE id='n_substack_source'"""
        )
        conn.commit()
        pending_ids = {row["id"] for row in dbmod.get_pending_items(conn)}
        assert "n_meta_source" in pending_ids
        assert "n_substack_source" not in pending_ids
        substack_row = conn.execute(
            "SELECT * FROM news_items WHERE id='n_substack_source'"
        ).fetchone()
        result = asyncio.run(run_pipeline.process_item(conn, substack_row))
        assert result == "skipped_target_scope"
        assert conn.execute(
            "SELECT COUNT(*) FROM drafts WHERE news_id='n_substack_source'"
        ).fetchone()[0] == 0


def test_owner_meta_submission_bypasses_relevance_drop_but_enters_quality_path(
    tmp_db, monkeypatch
):
    from src.scorer import NewsScore, ScoreBreakdown

    async def _low_score(*_args, **_kwargs):
        return NewsScore(
            confidence_score=0.2,
            editorial_note="owner-directed post",
            score_breakdown=ScoreBreakdown(
                data_density=0.2,
                strategic_signal=0.2,
                news_novelty=0.2,
                persona_fit=0.2,
            ),
        )

    monkeypatch.setattr(run_pipeline, "score_news", _low_score)
    calls = _patch_composable_path(
        monkeypatch,
        [_bundle("Owner 明確指定處理；內容仍需通過 deterministic quality guard。")],
    )
    with dbmod.get_conn() as conn:
        row = _seed_fetched_news(conn, "n_owner_low_score")
        conn.execute(
            """UPDATE news_items
                  SET feed_name='user_submission',tags='["user_submission","platform:threads"]'
                WHERE id='n_owner_low_score'"""
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM news_items WHERE id='n_owner_low_score'"
        ).fetchone()
        result = asyncio.run(
            run_pipeline.process_item(
                conn,
                row,
                compose_only=True,
                requested_platforms={"threads"},
            )
        )
        assert result == "queued"
        assert calls and calls[0][1]["platforms"] == ["threads"]
        draft = conn.execute(
            "SELECT status,queue_status,confidence_score FROM drafts WHERE news_id='n_owner_low_score'"
        ).fetchone()
        assert tuple(draft[:2]) == ("auto_approved", "queued")
        assert draft["confidence_score"] == 0.2
