from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_taiwan_official_feeds_have_bounded_authoritative_content_paths() -> None:
    config = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    official = {
        row["name"]: row
        for row in config["feeds"]
        if "primary-record" in (row.get("tags") or [])
    }
    assert set(official) == {
        "行政院 本院新聞",
        "行政院 消費警訊",
        "食藥署 本署新聞",
        "食藥署 闢謠專區",
        "證交所 官方訊息",
    }
    assert all(row["tier"] == "primary" for row in official.values())
    assert all(row["max_entries"] == 15 for row in official.values())
    assert official["食藥署 本署新聞"]["source_type"] == "article"
    assert official["食藥署 闢謠專區"]["source_type"] == "article"
    assert official["食藥署 本署新聞"]["min_word_count"] == 80
    assert official["食藥署 闢謠專區"]["min_word_count"] == 80
