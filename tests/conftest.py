"""
News Radar · pytest 共用 fixtures

設計原則：
  - 所有 fixture 都是 deterministic：不打網路、不動 production DB。
  - 測試資料存在 tests/fixtures/，寫實但精簡。
  - temp DB 走 tmp_path + init_db(), 每個測試互不干擾。

若 pytest 尚未安裝：`pip install pytest pytest-asyncio`
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
# 把 news_radar/ 加入 sys.path，tests 以 `from src.xxx import` 引用
sys.path.insert(0, str(_BASE))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def sample_html_en() -> str:
    """Simon Willison 風格的一篇 tech blog HTML fixture。"""
    path = FIXTURES / "sample_blog_en.html"
    return path.read_text(encoding="utf-8")


@pytest.fixture
def sample_html_paywall() -> str:
    """模擬被 paywall 擋下、只剩標題 + login prompt 的極短 HTML。"""
    path = FIXTURES / "sample_paywall.html"
    return path.read_text(encoding="utf-8")


@pytest.fixture
def minimal_config() -> dict:
    """最小可用的 config dict，給 cleaner/fetcher 吃。"""
    return {
        "feeds": [],
        "filters": {
            "min_word_count": 100,
            "max_age_hours": 168,
            "duplicate_similarity": 0.85,
        },
        "keywords": {
            "must_include_any": ["OpenAI", "Anthropic", "NVIDIA"],
            "must_exclude_any": ["crypto", "NFT"],
        },
    }


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """為每個測試建立一個獨立的 SQLite DB。
    monkeypatch 把 db.DB_PATH 指到 tmp 位置，避免汙染 production。
    """
    from src import db as dbmod

    tmp_db_path = tmp_path / "test_radar.db"
    schema_src = _BASE / "data" / "01_harvest" / "schema.sql"
    schema_dst = tmp_path / "schema.sql"
    schema_dst.write_text(schema_src.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setattr(dbmod, "DB_PATH", tmp_db_path)
    monkeypatch.setattr(dbmod, "SCHEMA_PATH", schema_dst)

    dbmod.init_db()
    yield tmp_db_path


@pytest.fixture(autouse=True)
def _isolate_live_runtime_markers(tmp_path_factory, monkeypatch):
    """測試不准碰 Mac 上真實的執行期狀態檔。

    2026-10-09：模擬「推送成功」的既有測試會走到清除 Substack 登入失效標記的路徑，
    清的是 data/substack_drafts/ 底下真的那一份——跑一次完整測試，排程的防空轉保護
    就被拿掉了。所有這類路徑一律導向暫存目錄。"""
    sandbox = tmp_path_factory.mktemp("runtime-markers")
    try:
        from substack_radar import compose
        monkeypatch.setattr(compose, "SUBSTACK_AUTH_MARKER", sandbox / ".substack_auth_failed")
    except Exception:
        pass
    try:
        from scripts import drain_substack
        monkeypatch.setattr(drain_substack, "ATTEMPTS_FILE", sandbox / ".substack_attempts.json")
        monkeypatch.setattr(drain_substack, "_substack_login_ok", lambda: True)
    except Exception:
        pass
