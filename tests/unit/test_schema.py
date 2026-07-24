"""
News Radar · Unit tests for src/schema.py

保險：Pydantic 欄位改名 / 預設值調整時，下游相容性會不會炸。
"""
from __future__ import annotations
import json

import pytest
from pydantic import ValidationError

from src.schema import NewsItem, HarvestReport, MultiPlatformDraft, PlatformVariant


def test_news_item_defaults():
    item = NewsItem(
        id="x1",
        feed_name="f",
        feed_tier="primary",
        url="https://example.com",
        title="t",
        published_at="2026-04-18T00:00:00+00:00",
        fetched_at="2026-04-19T00:00:00+00:00",
    )
    assert item.status == "fetched"
    assert item.word_count == 0
    assert item.tags == []
    assert item.clean_markdown is None


def test_news_item_requires_core_fields():
    with pytest.raises(ValidationError):
        NewsItem(id="x1", feed_name="f")  # type: ignore[call-arg]


def test_harvest_report_serializable():
    r = HarvestReport(
        started_at="2026-04-19T00:00:00+00:00",
        finished_at="2026-04-19T00:05:00+00:00",
        feeds_checked=10,
        items_found=30,
        items_new=5,
        items_dropped=25,
        drop_reasons={"too_short:40<100": 10, "no_keyword_match": 15},
    )
    j = r.model_dump_json()
    payload = json.loads(j)
    assert payload["items_new"] == 5
    assert payload["drop_reasons"]["no_keyword_match"] == 15


def test_platform_variant_char_count_required():
    # 避免 composer.py 忘記回填 char_count
    v = PlatformVariant(title="t", body="hello world", hashtags=["#ai"], char_count=11)
    assert v.char_count == 11


def test_multi_platform_draft_all_optional():
    d = MultiPlatformDraft()
    assert d.fb is None
    assert d.ig is None
    assert d.threads is None


def test_multi_platform_draft_drops_unsolicited_partial_variant_only():
    draft = MultiPlatformDraft.model_validate(
        {
            "threads": {
                "title": "t",
                "body": "complete",
                "hashtags": ["#台灣"],
                "char_count": 8,
            },
            "ig": {
                "title": "unrequested partial",
                "hashtags": ["#台灣"],
                "char_count": 20,
            },
        }
    )

    assert draft.threads is not None
    assert draft.threads.body == "complete"
    assert draft.ig is None
