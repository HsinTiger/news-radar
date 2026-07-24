"""Integration-ish test: 驗證 run_publish_queue._publish_one 會被 QualityGuard 攔下。

這邊不碰真實 Meta API（mock publisher）、也不碰 launch_notification_center
（靠 local_notify 在 Linux sandbox 預設就 no-op），只驗證：
    給一筆『platform_drafts 內容是 emergency_template』的 draft，
    _publish_one 會在 call publisher 前就 return OUTCOME_QUALITY_BLOCKED，並標 failed。

Phase 8.20 update：_publish_one 改回 string outcome code（不再是 bool）。
OUTCOME_QUALITY_BLOCKED 代表 guard 正常運作，對應 exit code 0（不是 workflow 失敗）。
"""
from __future__ import annotations

import asyncio
import sqlite3
import types
from datetime import datetime, timezone
from pathlib import Path

from src import content_quality_guard as guard_mod


# ---------- 共用 in-memory DB + schema ----------
def _fresh_memory_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema_path = Path(__file__).resolve().parents[2] / "data" / "01_harvest" / "schema.sql"
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    # 手動補 Phase 8.18 的 queue_status 欄位（schema.sql 是舊版）
    cols = {r[1] for r in conn.execute("PRAGMA table_info(drafts)").fetchall()}
    if "queue_status" not in cols:
        conn.execute("ALTER TABLE drafts ADD COLUMN queue_status TEXT")
    if "publish_at" not in cols:
        conn.execute("ALTER TABLE drafts ADD COLUMN publish_at TEXT")
    if "carousel_json" not in cols:
        conn.execute("ALTER TABLE drafts ADD COLUMN carousel_json TEXT")
    return conn


def _seed_one_templated_draft(conn):
    """寫一筆『news_items + drafts + platform_drafts』，
    platform_drafts 三列都是 emergency_template 文字。"""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO news_items
             (id, feed_name, feed_tier, url, title, published_at, fetched_at)
           VALUES ('n1','test','primary','https://a.example/b',
                   'Zero-Copy GPU Inference from WebAssembly on Apple Silicon',?,?)""",
        (now, now),
    )
    conn.execute(
        """INSERT INTO drafts
             (id, news_id, persona_version, generated_at, status,
              confidence_score, queue_status, publish_at)
           VALUES ('d1','n1','1.1',?, 'auto_approved', 1.0, 'queued', ?)""",
        (now, now),
    )
    tmpl = (
        "🚀 Zero-Copy GPU Inference from WebAssembly on Apple Silicon\n\n"
        "【系統代班速報】\n\n"
        "科技格局正在發生結構性位移，護城河的定義已從產品轉向生態數據。\n\n"
        "#科技戰略 #商業洞察 #數據驅動"
    )
    for pl in ("facebook", "instagram", "threads"):
        conn.execute(
            """INSERT INTO platform_drafts
                 (draft_id, platform, title, body, full_text, char_count, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("d1", pl, "🚀 Zero-Copy GPU Inference", tmpl, tmpl, len(tmpl), now),
        )
    conn.commit()


def test_publish_queue_blocks_templated_draft(monkeypatch):
    """_publish_one 對 emergency_template 內容必須 return False 且 mark failed。"""
    import run_publish_queue as rpq

    conn = _fresh_memory_conn()
    _seed_one_templated_draft(conn)

    # Mock publisher to explode if called (proves guard short-circuits before network)
    async def _never_called(*a, **kw):
        raise AssertionError("publisher must NOT be called when guard blocks")
    monkeypatch.setattr(rpq, "publish_to_fb", _never_called)
    monkeypatch.setattr(rpq, "publish_to_ig", _never_called)
    monkeypatch.setattr(rpq, "publish_to_threads", _never_called)

    # Build a fake 'row' like pick_freshest_queued returns (SELECT joined columns)
    row = conn.execute(
        """SELECT d.id, d.news_id, n.title AS news_title,
                  n.published_at AS news_published_at, n.og_image_url
             FROM drafts d JOIN news_items n ON d.news_id = n.id
            WHERE d.id='d1'"""
    ).fetchone()

    outcome = asyncio.run(rpq._publish_one(conn, row, dry_run=False))
    assert outcome == rpq.OUTCOME_QUALITY_BLOCKED, f"expected quality_blocked, got {outcome!r}"

    # After block, queue_status='failed'
    qs = conn.execute("SELECT queue_status FROM drafts WHERE id='d1'").fetchone()[0]
    assert qs == "failed", f"expected queue_status=failed, got {qs}"


def test_publish_queue_guard_includes_rendered_carousel_text(monkeypatch):
    import run_publish_queue as rpq
    from src.content_quality_guard import QualityIssue
    from src.schema import CarouselCards

    conn = _fresh_memory_conn()
    _seed_one_templated_draft(conn)
    conn.execute(
        "UPDATE platform_drafts SET full_text='caption passes isolated test' WHERE draft_id='d1'"
    )
    cards = CarouselCards(insight_statement="CAROUSEL_ONLY_TRAP")
    conn.execute(
        "UPDATE drafts SET carousel_json=? WHERE id='d1'",
        (cards.model_dump_json(),),
    )
    conn.commit()

    checked: list[str] = []

    def _guard(text, *args, **kwargs):
        checked.append(text)
        if "CAROUSEL_ONLY_TRAP" in text:
            return [
                QualityIssue(
                    code="carousel_trap",
                    severity="block",
                    message="test",
                    evidence="CAROUSEL_ONLY_TRAP",
                )
            ]
        return []

    async def _never_called(*args, **kwargs):
        raise AssertionError("publisher must not receive unguarded carousel text")

    monkeypatch.setattr(rpq, "check_quality", _guard)
    monkeypatch.setattr(rpq, "publish_to_fb", _never_called)
    monkeypatch.setattr(rpq, "publish_to_ig", _never_called)
    monkeypatch.setattr(rpq, "publish_to_threads", _never_called)
    row = conn.execute(
        """SELECT d.*,n.title AS news_title,n.published_at AS news_published_at,
                  n.og_image_url,n.topic_category
             FROM drafts d JOIN news_items n ON n.id=d.news_id
            WHERE d.id='d1'"""
    ).fetchone()

    outcome = asyncio.run(rpq._publish_one(conn, row, dry_run=False))

    assert outcome == rpq.OUTCOME_QUALITY_BLOCKED
    assert checked and all("CAROUSEL_ONLY_TRAP" in text for text in checked)


def test_publish_queue_lets_healthy_draft_through(monkeypatch):
    """對照組：healthy 內容不該被 guard 攔下（publisher 會被呼叫）。"""
    import run_publish_queue as rpq

    conn = _fresh_memory_conn()
    now = datetime.now(timezone.utc).isoformat()
    healthy = (
        "Anthropic 今日發佈 Claude Opus 4.7，SWE-bench Verified 拿下 78.2%，"
        "較前一代提升 6.1 個百分點。Agent 場景上單次任務平均呼叫工具 12.4 次、"
        "錯誤重試率較 4.6 下降 31%。\n\n#Claude"
    )
    conn.execute(
        """INSERT INTO news_items
             (id, feed_name, feed_tier, url, title, published_at, fetched_at)
           VALUES ('n2','test','primary','https://a.example/c','Anthropic 發表 Claude Opus 4.7',?,?)""",
        (now, now),
    )
    conn.execute(
        """INSERT INTO drafts
             (id, news_id, persona_version, generated_at, status,
              confidence_score, queue_status, publish_at)
           VALUES ('d2','n2','1.1',?, 'auto_approved', 1.0, 'queued', ?)""",
        (now, now),
    )
    for pl in ("facebook", "instagram", "threads"):
        conn.execute(
            """INSERT INTO platform_drafts
                 (draft_id, platform, full_text, created_at)
               VALUES ('d2', ?, ?, ?)""",
            (pl, healthy, now),
        )
    conn.commit()

    # Track publisher calls
    call_log = []
    async def _fake_ok(*a, **kw):
        call_log.append(1)
        return {"success": True, "id": f"post_{len(call_log)}"}
    monkeypatch.setattr(rpq, "publish_to_fb", _fake_ok)
    monkeypatch.setattr(rpq, "publish_to_ig", _fake_ok)
    monkeypatch.setattr(rpq, "publish_to_threads", _fake_ok)
    async def _fake_prepare(**kwargs):
        return {"image_url": kwargs.get("original_image_url")}
    monkeypatch.setattr(rpq, "prepare_publish_image", _fake_prepare)

    row = conn.execute(
        """SELECT d.id, d.news_id, n.title AS news_title,
                  n.published_at AS news_published_at, n.og_image_url
             FROM drafts d JOIN news_items n ON d.news_id = n.id
            WHERE d.id='d2'"""
    ).fetchone()

    # seed an og_image_url so IG/Threads don't early-reject
    conn.execute("UPDATE news_items SET og_image_url='https://example.com/x.jpg' WHERE id='n2'")
    conn.commit()
    row = conn.execute(
        """SELECT d.id, d.news_id, n.title AS news_title,
                  n.published_at AS news_published_at, n.og_image_url
             FROM drafts d JOIN news_items n ON d.news_id = n.id
            WHERE d.id='d2'"""
    ).fetchone()

    outcome = asyncio.run(rpq._publish_one(conn, row, dry_run=False))
    assert outcome == rpq.OUTCOME_PUBLISHED, f"expected published, got {outcome!r}"
    assert len(call_log) == 3, f"expected 3 publisher calls, got {len(call_log)}"


def test_recovery_publishes_carousel_only_to_instagram(monkeypatch):
    """FB/Threads stay native feed posts while IG receives the five cards."""
    import run_publish_queue as rpq
    from src.schema import CarouselCards

    monkeypatch.setenv("AUTOMATION_MODE", "recovery")
    conn = _fresh_memory_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO news_items(
             id,feed_name,feed_tier,url,title,published_at,fetched_at,
             og_image_url,topic_category
           ) VALUES(
             'n-native','test','primary','https://a.example/native',
             '食藥署公布產品清單',?,?,
             'https://example.com/native.jpg','tw_politics'
           )""",
        (now, now),
    )
    cards = CarouselCards(
        insight_statement="食藥署公布具體後果",
        insight_support="食藥署公告說明事件經過",
        stat_number="19批",
        stat_caption="食藥署公布的合格批次",
        takeaways=["消費者先核對產品批號"],
        key_figures=[
            {"label": "合格批次", "value": "19批"},
            {"label": "產品項目", "value": "501項"},
        ],
    )
    conn.execute(
        """INSERT INTO drafts(
             id,news_id,persona_version,generated_at,status,confidence_score,
             queue_status,publish_at,carousel_json
           ) VALUES(
             'd-native','n-native','1.1',?,'auto_approved',1.0,
             'queued',?,?
           )""",
        (now, now, cards.model_dump_json()),
    )
    for platform in ("facebook", "instagram", "threads"):
        conn.execute(
            """INSERT INTO platform_drafts(
                 draft_id,platform,title,full_text,created_at
               ) VALUES('d-native',?,'食藥署公布產品清單','合格內文',?)""",
            (platform, now),
        )
        conn.execute(
            """INSERT INTO recovery_experiments(
                 id,draft_id,platform,experiment_type,hypothesis,
                 baseline_primary_metric,baseline_captured_at,content_format,
                 created_at
               ) VALUES(?,?,?,?,?,'views',?,?,?)""",
            (
                f"rx-{platform}",
                "d-native",
                platform,
                "utility",
                "native-format-test",
                now,
                "carousel" if platform == "instagram" else "feed",
                now,
            ),
        )
    conn.commit()

    calls: list[str] = []

    async def _feed_ok(*_args, **_kwargs):
        calls.append("feed")
        return {"success": True, "id": f"feed-{len(calls)}"}

    async def _ig_carousel_ok(*_args, **_kwargs):
        calls.append("instagram-carousel")
        return {"success": True, "id": "ig-carousel"}

    async def _forbidden_carousel(*_args, **_kwargs):
        raise AssertionError("Recovery FB/Threads must not publish a carousel")

    async def _forbidden_ig_feed(*_args, **_kwargs):
        raise AssertionError("Recovery Instagram must not fall back to feed")

    async def _prepare(**kwargs):
        return {"image_url": kwargs.get("original_image_url")}

    monkeypatch.setattr(rpq, "check_quality", lambda *_a, **_kw: [])
    monkeypatch.setattr(rpq, "check_platform_style", lambda *_a, **_kw: [])
    monkeypatch.setattr(rpq, "prepare_publish_image", _prepare)
    monkeypatch.setattr(rpq, "render_cards", lambda **_kw: [Path(f"{i}.png") for i in range(5)])
    monkeypatch.setattr(
        rpq,
        "upload_cards",
        lambda *_a, **_kw: [f"https://example.com/{i}.png" for i in range(5)],
    )
    monkeypatch.setattr(rpq, "publish_to_fb", _feed_ok)
    monkeypatch.setattr(rpq, "publish_to_threads", _feed_ok)
    monkeypatch.setattr(rpq, "publish_to_ig", _forbidden_ig_feed)
    monkeypatch.setattr(rpq, "publish_ig_carousel", _ig_carousel_ok)
    monkeypatch.setattr(rpq, "publish_fb_carousel", _forbidden_carousel)
    monkeypatch.setattr(rpq, "publish_threads_carousel", _forbidden_carousel)

    row = conn.execute(
        """SELECT d.*,n.title AS news_title,n.published_at AS news_published_at,
                  n.og_image_url,n.topic_category
             FROM drafts d JOIN news_items n ON n.id=d.news_id
            WHERE d.id='d-native'"""
    ).fetchone()
    outcome = asyncio.run(
        rpq._publish_one(
            conn,
            row,
            platforms={"facebook", "instagram", "threads"},
        )
    )

    assert outcome == rpq.OUTCOME_PUBLISHED
    assert calls.count("feed") == 2
    assert calls.count("instagram-carousel") == 1
    formats = {
        row["platform"]: row["actual_format"]
        for row in conn.execute(
            "SELECT platform,actual_format FROM recovery_experiments"
        )
    }
    assert formats == {
        "facebook": "feed",
        "instagram": "carousel",
        "threads": "feed",
    }

    # A failed IG carousel must remain queued for retry; Recovery may not
    # silently substitute a single-image feed post and corrupt the experiment.
    conn.execute(
        """INSERT INTO drafts(
             id,news_id,persona_version,generated_at,status,confidence_score,
             queue_status,publish_at,carousel_json
           ) VALUES(
             'd-native-fail','n-native','1.1',?,'auto_approved',1.0,
             'queued',?,?
           )""",
        (now, now, cards.model_dump_json()),
    )
    conn.execute(
        """INSERT INTO platform_drafts(
             draft_id,platform,title,full_text,created_at
           ) VALUES(
             'd-native-fail','instagram','食藥署公布產品清單','合格內文',?
           )""",
        (now,),
    )
    conn.execute(
        """INSERT INTO recovery_experiments(
             id,draft_id,platform,experiment_type,hypothesis,
             baseline_primary_metric,baseline_captured_at,content_format,
             created_at
           ) VALUES(
             'rx-instagram-fail','d-native-fail','instagram','utility',
             'native-format-failure-test','reach',?,'carousel',?
           )""",
        (now, now),
    )
    conn.commit()

    async def _ig_carousel_fail(*_args, **_kwargs):
        return {"success": False, "error": {"code": 500}}

    monkeypatch.setattr(rpq, "publish_ig_carousel", _ig_carousel_fail)
    failed_row = conn.execute(
        """SELECT d.*,n.title AS news_title,n.published_at AS news_published_at,
                  n.og_image_url,n.topic_category
             FROM drafts d JOIN news_items n ON n.id=d.news_id
            WHERE d.id='d-native-fail'"""
    ).fetchone()
    failed_outcome = asyncio.run(
        rpq._publish_one(conn, failed_row, platforms={"instagram"})
    )
    assert failed_outcome == rpq.OUTCOME_ALL_PLATFORMS_FAILED
    assert conn.execute(
        "SELECT queue_status FROM drafts WHERE id='d-native-fail'"
    ).fetchone()[0] == "queued"
    assert conn.execute(
        "SELECT actual_format FROM recovery_experiments "
        "WHERE draft_id='d-native-fail'"
    ).fetchone()[0] is None


# ---------- Phase 8.20 追加：exit code 語義測試 ----------

def test_publish_queue_all_platforms_fail_returns_all_failed_outcome(monkeypatch):
    """若 publisher 三個平台都失敗，outcome 必須是 ALL_PLATFORMS_FAILED（workflow red）。"""
    import run_publish_queue as rpq

    conn = _fresh_memory_conn()
    now = datetime.now(timezone.utc).isoformat()
    healthy = (
        "NVIDIA 宣布 Blackwell B200 量產，首批 10 萬片已交付 Hyperscaler。"
        "每片 BoM 約 3 萬美金，Microsoft、Meta、Google、Amazon 四家合計 85%。\n\n#NVIDIA"
    )
    conn.execute(
        """INSERT INTO news_items
             (id, feed_name, feed_tier, url, title, published_at, fetched_at, og_image_url)
           VALUES ('n3','test','primary','https://a.example/d','Blackwell B200 量產',?,?,?)""",
        (now, now, "https://example.com/y.jpg"),
    )
    conn.execute(
        """INSERT INTO drafts
             (id, news_id, persona_version, generated_at, status,
              confidence_score, queue_status, publish_at)
           VALUES ('d3','n3','1.1',?, 'auto_approved', 1.0, 'queued', ?)""",
        (now, now),
    )
    for pl in ("facebook", "instagram", "threads"):
        conn.execute(
            """INSERT INTO platform_drafts
                 (draft_id, platform, full_text, created_at)
               VALUES ('d3', ?, ?, ?)""",
            (pl, healthy, now),
        )
    conn.commit()

    async def _fake_fail(*a, **kw):
        return {"success": False, "error": {"code": 500, "msg": "simulated API outage"}}
    monkeypatch.setattr(rpq, "publish_to_fb", _fake_fail)
    monkeypatch.setattr(rpq, "publish_to_ig", _fake_fail)
    monkeypatch.setattr(rpq, "publish_to_threads", _fake_fail)
    async def _fake_prepare(**kwargs):
        return {"image_url": kwargs.get("original_image_url")}
    monkeypatch.setattr(rpq, "prepare_publish_image", _fake_prepare)

    row = conn.execute(
        """SELECT d.id, d.news_id, n.title AS news_title,
                  n.published_at AS news_published_at, n.og_image_url
             FROM drafts d JOIN news_items n ON d.news_id = n.id
            WHERE d.id='d3'"""
    ).fetchone()

    outcome = asyncio.run(rpq._publish_one(conn, row, dry_run=False))
    assert outcome == rpq.OUTCOME_ALL_PLATFORMS_FAILED, \
        f"expected all_platforms_failed, got {outcome!r}"

    # 保留 queued，讓下一個同平台 cycle 自動 retry；workflow 本身仍 red。
    qs = conn.execute("SELECT queue_status FROM drafts WHERE id='d3'").fetchone()[0]
    assert qs == "queued", f"expected queue_status=queued, got {qs}"


def test_partial_failure_retries_only_missing_platforms(monkeypatch):
    """FB 成功、IG/Threads 失敗時不可把整個 draft 假標 published。"""
    import run_publish_queue as rpq

    conn = _fresh_memory_conn()
    now = datetime.now(timezone.utc).isoformat()
    healthy = (
        "新產品將推論延遲降到原本的一半，官方文件同時揭露功耗限制與部署條件。"
        "這次更新真正值得追蹤的是量產後的總持有成本。\n\n#AI"
    )
    conn.execute(
        """INSERT INTO news_items
             (id,feed_name,feed_tier,url,title,published_at,fetched_at,og_image_url)
           VALUES('n-part','test','primary','https://a.example/part','Partial retry',?,?,?)""",
        (now, now, "https://example.com/part.jpg"),
    )
    conn.execute(
        """INSERT INTO drafts
             (id,news_id,persona_version,generated_at,status,confidence_score,
              queue_status,publish_at)
           VALUES('d-part','n-part','1.1',?,'auto_approved',1.0,'queued',?)""",
        (now, now),
    )
    for platform in ("facebook", "instagram", "threads"):
        conn.execute(
            """INSERT INTO platform_drafts(draft_id,platform,full_text,created_at)
               VALUES('d-part',?,?,?)""",
            (platform, healthy, now),
        )
    conn.commit()

    async def _prepare(**kwargs):
        return {"image_url": kwargs.get("original_image_url")}

    async def _fb_ok(*_args, **_kwargs):
        return {"success": True, "id": "fb-ok"}

    async def _fail(*_args, **_kwargs):
        return {"success": False, "error": {"code": 500}}

    monkeypatch.setattr(rpq, "prepare_publish_image", _prepare)
    monkeypatch.setattr(rpq, "publish_to_fb", _fb_ok)
    monkeypatch.setattr(rpq, "publish_to_ig", _fail)
    monkeypatch.setattr(rpq, "publish_to_threads", _fail)
    row = conn.execute(
        """SELECT d.*,n.title AS news_title,n.published_at AS news_published_at,
                  n.og_image_url,n.topic_category
             FROM drafts d JOIN news_items n ON n.id=d.news_id
            WHERE d.id='d-part'"""
    ).fetchone()

    first = asyncio.run(
        rpq._publish_one(
            conn,
            row,
            platforms={"facebook", "instagram", "threads"},
        )
    )
    assert first == rpq.OUTCOME_PARTIAL_FAILURE
    assert conn.execute(
        "SELECT queue_status FROM drafts WHERE id='d-part'"
    ).fetchone()[0] == "queued"
    assert rpq.dbmod.pending_publish_platforms(conn, "d-part") == {
        "instagram", "threads"
    }

    async def _fb_must_not_repeat(*_args, **_kwargs):
        raise AssertionError("successful Facebook tuple must be idempotently skipped")

    async def _ok(*_args, **_kwargs):
        return {"success": True, "id": "retry-ok"}

    monkeypatch.setattr(rpq, "publish_to_fb", _fb_must_not_repeat)
    monkeypatch.setattr(rpq, "publish_to_ig", _ok)
    monkeypatch.setattr(rpq, "publish_to_threads", _ok)
    second = asyncio.run(
        rpq._publish_one(
            conn,
            row,
            platforms={"facebook", "instagram", "threads"},
        )
    )
    assert second == rpq.OUTCOME_PUBLISHED
    assert conn.execute(
        "SELECT queue_status FROM drafts WHERE id='d-part'"
    ).fetchone()[0] == "published"
    assert rpq.dbmod.pending_publish_platforms(conn, "d-part") == set()


def test_publish_queue_no_platform_drafts_returns_no_platform_outcome():
    """若 draft 沒有對應的 platform_drafts（資料異常），outcome 必須是 NO_PLATFORM_DRAFTS。"""
    import run_publish_queue as rpq

    conn = _fresh_memory_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO news_items
             (id, feed_name, feed_tier, url, title, published_at, fetched_at)
           VALUES ('n4','test','primary','https://a.example/e','孤兒 draft',?,?)""",
        (now, now),
    )
    conn.execute(
        """INSERT INTO drafts
             (id, news_id, persona_version, generated_at, status,
              confidence_score, queue_status, publish_at)
           VALUES ('d4','n4','1.1',?, 'auto_approved', 1.0, 'queued', ?)""",
        (now, now),
    )
    conn.commit()
    # 故意不 seed platform_drafts

    row = conn.execute(
        """SELECT d.id, d.news_id, n.title AS news_title,
                  n.published_at AS news_published_at, n.og_image_url
             FROM drafts d JOIN news_items n ON d.news_id = n.id
            WHERE d.id='d4'"""
    ).fetchone()

    outcome = asyncio.run(rpq._publish_one(conn, row, dry_run=False))
    assert outcome == rpq.OUTCOME_NO_PLATFORM_DRAFTS, \
        f"expected no_platform_drafts, got {outcome!r}"

    qs = conn.execute("SELECT queue_status FROM drafts WHERE id='d4'").fetchone()[0]
    assert qs == "failed", f"expected queue_status=failed, got {qs}"


def test_outcome_constants_are_distinct():
    """OUTCOME_* 常數不可撞名，否則 exit code 映射會崩。"""
    import run_publish_queue as rpq
    outcomes = {
        rpq.OUTCOME_PUBLISHED,
        rpq.OUTCOME_QUALITY_BLOCKED,
        rpq.OUTCOME_NO_PLATFORM_DRAFTS,
        rpq.OUTCOME_PARTIAL_FAILURE,
        rpq.OUTCOME_ALL_PLATFORMS_FAILED,
    }
    assert len(outcomes) == 5, f"expected 5 distinct outcomes, got {outcomes}"
