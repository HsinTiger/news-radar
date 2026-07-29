import sqlite3

import pytest

from scripts import verify_meta_carousel as verifier
from src.schema import CarouselCards


POST_IDS = {
    "facebook": "page_123",
    "instagram": "ig_123",
    "threads": "threads_123",
}


def _children():
    return [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}]


def test_remote_shapes_require_exactly_three_children_and_permalink() -> None:
    fb = verifier._proof(
        "facebook",
        {
            "id": POST_IDS["facebook"],
            "permalink_url": "https://facebook.test/post",
            "attachments": {
                "data": [
                    {
                        "media_type": "album",
                        "subattachments": {"data": _children()},
                    }
                ]
            },
        },
        POST_IDS["facebook"],
    )
    ig = verifier._proof(
        "instagram",
        {
            "id": POST_IDS["instagram"],
            "media_type": "CAROUSEL_ALBUM",
            "permalink": "https://instagram.test/post",
            "children": {"data": _children()},
        },
        POST_IDS["instagram"],
    )
    threads = verifier._proof(
        "threads",
        {
            "id": POST_IDS["threads"],
            "media_type": "CAROUSEL",
            "permalink": "https://threads.test/post",
            "children": {"data": _children()},
        },
        POST_IDS["threads"],
    )

    assert {fb["child_count"], ig["child_count"], threads["child_count"]} == {3}
    with pytest.raises(ValueError, match="expected 3 remote children"):
        verifier._proof(
            "instagram",
            {
                "id": POST_IDS["instagram"],
                "media_type": "CAROUSEL_ALBUM",
                "permalink": "https://instagram.test/post",
                "children": {"data": _children()[:2]},
            },
            POST_IDS["instagram"],
        )


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE news_items(
          id TEXT PRIMARY KEY,topic_category TEXT,title TEXT
        );
        CREATE TABLE drafts(
          id TEXT PRIMARY KEY,news_id TEXT,carousel_json TEXT
        );
        CREATE TABLE publish_log(
          id INTEGER PRIMARY KEY AUTOINCREMENT,draft_id TEXT,platform TEXT,
          platform_post_id TEXT,success INTEGER
        );
        CREATE TABLE recovery_experiments(
          id TEXT PRIMARY KEY,draft_id TEXT,platform TEXT,experiment_type TEXT,
          hypothesis TEXT,baseline_followers INTEGER,baseline_primary_metric TEXT,
          baseline_primary_value REAL,baseline_captured_at TEXT,
          content_format TEXT,actual_format TEXT,actual_format_at TEXT,
          topic TEXT,created_at TEXT,UNIQUE(draft_id,platform)
        );
        """
    )
    carousel = CarouselCards(
        insight_statement="有一項可驗證主張",
        insight_support="官方資料提供具體支持",
        source_attribution="來源：官方資料",
        stat_number="3",
        stat_caption="三項可核對資料",
        takeaways=["核對原始公告", "比較前後差異"],
        reader_question="你會先核對哪一項？",
    )
    conn.execute("INSERT INTO news_items VALUES('n1','tw_politics','公共議題')")
    conn.execute(
        "INSERT INTO drafts VALUES('d1','n1',?)", (carousel.model_dump_json(),)
    )
    for platform, post_id in POST_IDS.items():
        conn.execute(
            "INSERT INTO publish_log(draft_id,platform,platform_post_id,success) "
            "VALUES('d1',?,?,1)",
            (platform, post_id),
        )
    conn.commit()
    return conn


def test_canonical_format_is_recorded_only_for_matching_remote_proof() -> None:
    conn = _conn()
    proofs = {
        platform: {
            "platform": platform,
            "post_id": post_id,
            "readable": True,
            "child_count": 3,
        }
        for platform, post_id in POST_IDS.items()
    }
    verifier.record_canonical_proof(
        conn,
        draft_id="d1",
        post_ids=POST_IDS,
        proofs=proofs,
        observed_at="2026-07-29T14:00:00+00:00",
    )

    rows = conn.execute(
        "SELECT platform,content_format,actual_format FROM recovery_experiments "
        "ORDER BY platform"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("facebook", "carousel", "carousel"),
        ("instagram", "carousel", "carousel"),
        ("threads", "carousel", "carousel"),
    ]

    bad_ids = {**POST_IDS, "threads": "threads_wrong"}
    with pytest.raises(ValueError, match="canonical post id mismatch"):
        verifier.record_canonical_proof(
            conn,
            draft_id="d1",
            post_ids=bad_ids,
            proofs=proofs,
            observed_at="2026-07-29T14:01:00+00:00",
        )

