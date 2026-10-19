"""2026-10-17 依後台數據＋研究的呈現方式更新：字數、冒號標題、分區、發文時間實驗。"""
import json
from datetime import date, datetime

from substack_radar import composer
from substack_radar.compose import substack_section_id_for
from substack_radar.daily_publish import send_arm, slot_now


def test_podcast_and_company_lengths_ignore_global_cap(monkeypatch):
    monkeypatch.setenv("SUBSTACK_WORD_CAP", "3500")
    assert composer.word_range_for(composer.PODCAST_PROFILE) == (3000, 4000)   # 2026-10-19 加長
    assert composer.word_range_for(composer.COMPANY_PROFILE) == (4000, 5500)
    floor, cap = composer.word_range_for(composer.WEEKLY_PROFILE)   # 主編精選照舊吃全域上限
    assert cap == 3500


def test_section_routing_is_opt_in(monkeypatch):
    monkeypatch.delenv("SUBSTACK_SECTION_IDS", raising=False)
    assert substack_section_id_for("嬌生：淨利比本業賺的還多？｜賺錢有道") is None
    monkeypatch.setenv("SUBSTACK_SECTION_IDS", json.dumps({"賺錢有道": 11, "吹牛免稅": 22}))
    assert substack_section_id_for("嬌生：淨利比本業賺的還多？｜賺錢有道") == 11
    assert substack_section_id_for("吹牛免稅｜印度女性不上班") == 22
    assert substack_section_id_for("沒有專欄名的標題") is None


def test_send_time_experiment_alternates_by_week_and_ends():
    assert send_arm(date(2026, 10, 17)) == "morning"            # 實驗前
    assert send_arm(date(2026, 10, 19)) == "morning"            # 第 43 週
    assert send_arm(date(2026, 10, 27)) == "evening"            # 第 44 週
    assert send_arm(date(2026, 11, 16)) == "morning"            # 實驗後
    assert slot_now(datetime(2026, 10, 27, 7, 30)) == "morning"
    assert slot_now(datetime(2026, 10, 27, 17, 0)) == "evening"


def test_column_intro_matches_fb_copy_and_header_renders():
    from substack_radar import fb_follow
    from substack_radar.compose import COLUMN_INTRO, _column_header
    assert all(fb_follow.COLUMN_DESC[k] == v for k, v in COLUMN_INTRO.items())   # FB 另外保留舊名「主編精選」
    h = _column_header("他治癒了部下｜吹牛免稅", source_title="PBD Podcast #873", source_url="https://youtu.be/x")
    assert h.startswith("> **吹牛免稅**｜") and "本篇根據：[PBD Podcast #873](https://youtu.be/x)" in h
    assert "🧠" not in h and "產文路線" not in h          # 發布時不能被當成 AI 痕跡拿掉
    assert _column_header("沒有專欄名") == ""
