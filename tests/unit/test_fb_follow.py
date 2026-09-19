"""FB 跟發線的確定性部分。每一條都對應一個「發出去才發現」會出事的情境。"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from substack_radar import fb_follow
from substack_radar.fb_cards import LayoutError, hook_card, point_card, render_pair, split_lines
from substack_radar.fb_follow import (
    Piece, SourceInfo, deterministic_issues, finalize, split_column,
)

ARTICLE = "毛利率 58% 連 4 季站上高檔，營收 1,200 億。Joe Hudson 說控制會召喚無助感。"


# ---------------- 圖卡：不跑版 ----------------

def _render(tmp_path, hook="越拚命，／為何越失控？", point="控制手段，／最後會召喚無助感", figure=""):
    return render_pair(hook=hook, point=point, figure=figure, column="吹牛免稅", mode="podcast",
                       topic_category="other", title="越拚命，為何越失控", out_dir=tmp_path)


def test_two_cards_rendered_at_2_to_1(tmp_path):
    from PIL import Image
    paths = _render(tmp_path)
    assert [p.name for p in paths] == ["fb_card1.png", "fb_card2.png"]
    assert all(Image.open(p).size == (1600, 800) for p in paths)


def test_hook_too_long_is_rejected_not_truncated(tmp_path):
    with pytest.raises(LayoutError):
        _render(tmp_path, hook="這是一句很長很長很長很長很長的鉤子")


def test_writer_controls_line_breaks():
    # 自動斷行曾斷出「越拚命，為／何越失控」
    assert split_lines("越拚命，／為何越失控？", 2, "鉤子") == ["越拚命，", "為何越失控？"]


def test_line_starting_with_punctuation_is_rejected():
    with pytest.raises(LayoutError):
        split_lines("好消息／，但是", 2, "鉤子")


def test_too_many_lines_rejected():
    with pytest.raises(LayoutError):
        split_lines("一／二／三", 2, "鉤子")


def test_line_too_wide_for_column_is_rejected(tmp_path):
    with pytest.raises(LayoutError):
        _render(tmp_path, hook="一行十二個字的鉤子不分行啊")


def test_source_line_dropped_rather_than_overflowing(tmp_path):
    # 出處行放不下就不畫，不能因為它跑版
    long_show = "原始節目｜" + "Very Long Podcast Name That Cannot Fit" * 2
    _render(tmp_path)  # baseline
    render_pair(hook="越拚命，／為何越失控？", point="重點", figure="", column="吹牛免稅",
                mode="podcast", topic_category="other", title="x", out_dir=tmp_path,
                source_options=(long_show,))


# ---------------- 貼文閘門 ----------------

def test_invented_number_is_flagged():
    issues = deterministic_issues("毛利率其實是 62%，" + "字" * 200, ARTICLE)
    assert any("62" in i for i in issues)


def test_numbers_from_article_pass():
    assert not [i for i in deterministic_issues("毛利率 58% 連 4 季，營收 1,200 億。" + "字" * 200, ARTICLE)
                if "數字" in i]


def test_fabricated_trade_is_flagged():
    issues = deterministic_issues("我去年買了一堆，" + "字" * 200, ARTICLE)
    assert any("交易經歷" in i for i in issues)


def test_markdown_url_hashtag_flagged():
    issues = deterministic_issues("**重點** https://x.com #標籤 " + "字" * 200, ARTICLE)
    assert len(issues) >= 3


def test_must_name_the_original_show():
    src = SourceInfo(kind="youtube", show="My First Million", url="https://youtu.be/x")
    assert any("My First Million" in i for i in deterministic_issues("字" * 260, ARTICLE, src))
    assert not any("原始節目" in i for i in
                   deterministic_issues("My First Million 這集" + "字" * 260, ARTICLE, src))


# ---------------- 連結與專欄 ----------------

def test_only_substack_cta_no_source_link():
    # 信哥 2026-09-20：FB 很擠，不附原始節目連結，只留訂閱 CTA
    src = SourceInfo(kind="youtube", show="All-In", url="https://youtube.com/watch?v=abc")
    text = finalize("內文", "吹牛免稅", "draft", None, src)
    assert "youtube.com" not in text and "hsin73.substack.com" in text and "訂閱" in text
    assert text.rstrip().endswith("#吹牛免稅 #主力爸爸我錯了")


def test_em_dash_replaced_and_tells_detected():
    text = finalize("講完——然後呢", "吹牛免稅", "draft", None)
    assert "——" not in text
    assert fb_follow.ai_tells("故事是這樣的：與其擔心不如") == ["故事是這樣的", "與其"]


def test_rules_ban_ai_openers():
    assert "故事是這樣的" in fb_follow.FB_RULES and "150 到 300 字" in fb_follow.FB_RULES


def test_published_post_links_to_article():
    text = finalize("內文", "賺錢有道", "published", "https://hsin73.substack.com/p/9ed")
    assert "/p/9ed" in text


def test_column_suffix_and_legacy_prefix():
    assert split_column("越拚命，為何越失控｜吹牛免稅") == ("吹牛免稅", "越拚命，為何越失控")
    assert split_column("賺錢有道｜台積電還能不能買？") == ("賺錢有道", "台積電還能不能買？")
    assert split_column("沒有專欄的標題") == ("", "沒有專欄的標題")


# ---------------- 挑稿 ----------------

def _mk(tmp_path, day, name, created, **meta):
    d = tmp_path / day / name
    d.mkdir(parents=True)
    (d / "Article_Full.md").write_text("x", encoding="utf-8")
    base = {"title": f"{name}｜吹牛免稅", "mode": "podcast", "created_at": created.isoformat(),
            "source": {"id": name}, "substack_draft_id": "1"}
    base.update(meta)
    (d / "metadata.json").write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    return d


def test_scan_respects_cooldown_dedupe_and_fact_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(fb_follow, "DRAFTS_DIR", tmp_path)
    monkeypatch.setattr(fb_follow, "SINCE", "2026-09-01")
    now = datetime(2026, 9, 20, 12, 0)
    _mk(tmp_path, "2026-09-20", "fresh", now - timedelta(hours=1))                # 冷卻中
    _mk(tmp_path, "2026-09-19", "ok", now - timedelta(hours=20))                  # 可發
    _mk(tmp_path, "2026-09-19", "bad", now - timedelta(hours=20),
        audit_warnings=["[品質迴圈未通過] E1：假出處"])                            # 事實沒過
    _mk(tmp_path, "2026-09-18", "old_v", now - timedelta(hours=40), source={"ticker": "3034"})
    _mk(tmp_path, "2026-09-19", "new_v", now - timedelta(hours=18), source={"ticker": "3034"})
    _mk(tmp_path, "2026-09-02", "stale", now - timedelta(days=18))                 # 太舊
    got = [c.folder.name for c in fb_follow.scan(now=now, ledger={})]
    assert got == ["ok", "new_v"]


def test_already_posted_topic_not_reposted(tmp_path, monkeypatch):
    monkeypatch.setattr(fb_follow, "DRAFTS_DIR", tmp_path)
    monkeypatch.setattr(fb_follow, "SINCE", "2026-09-01")
    now = datetime(2026, 9, 20, 12, 0)
    _mk(tmp_path, "2026-09-19", "rerun", now - timedelta(hours=20), source={"ticker": "2330"})
    ledger = {"2026-09-18/first": {"status": "posted", "key": "company:2330"}}
    assert fb_follow.scan(now=now, ledger=ledger) == []


def test_deleted_draft_is_not_posted():
    class Api:
        def get_draft(self, _):
            raise Exception("APIError(code=404): Draft not found")
    cand = fb_follow.Candidate(folder=Path("x"), meta={}, created_at=datetime.now(),
                               column="吹牛免稅", headline="h", key="k", draft_id="123")
    assert fb_follow.remote_status(cand, api=Api()) == ("deleted", None)


def test_unknown_draft_id_is_not_posted():
    cand = fb_follow.Candidate(folder=Path("x"), meta={}, created_at=datetime.now(),
                               column="吹牛免稅", headline="h", key="k", draft_id=None)
    assert fb_follow.remote_status(cand, api=object())[0] == "unknown"


def test_opinion_about_others_is_not_a_fake_trade():
    assert not any("交易經歷" in i for i in deterministic_issues("我覺得公司買了太多設備，" + "字" * 200, ARTICLE))


def test_cn_numeral_in_article_counts_as_source():
    # 文章寫「十四小時」，貼文寫「14 小時」不是捏造
    assert not any("14" in i for i in deterministic_issues("每天工作 14 小時，" + "字" * 200, "工時長達十四小時"))


def test_source_duration_is_citable():
    src = SourceInfo(kind="youtube", show="My First Million", url="u", duration_s=5166)
    assert src.duration_text == "1 小時 26 分"
    post = "My First Million 這集 1 小時 26 分的訪談，" + "字" * 200
    assert not any("找不到" in i for i in deterministic_issues(post, ARTICLE, src))


def test_writer_outage_does_not_burn_attempts(tmp_path, monkeypatch):
    monkeypatch.setattr(fb_follow, "DRAFTS_DIR", tmp_path)
    monkeypatch.setattr(fb_follow, "LEDGER_PATH", tmp_path / ".fb_posted.json")
    folder = _mk(tmp_path, "2026-09-19", "x", datetime.now() - timedelta(hours=20))
    monkeypatch.setattr(fb_follow, "remote_status", lambda c: ("draft", None))
    def boom(_):
        raise RuntimeError("寫手鏈全部失敗：429")
    monkeypatch.setattr(fb_follow, "compose_post", boom)
    assert fb_follow.run(dry_run=False, only=str(folder)) == 2
    assert not (tmp_path / ".fb_posted.json").exists()


def test_unknown_english_name_is_flagged():
    art = "Joe Hudson 指導過 Sam Altman。"
    issues = deterministic_issues("McKinsey 報告說 Joe Hudson 很強，" + "字" * 200, art)
    assert any("McKinsey" in i for i in issues)
    assert not any("找不到：" in i and "Hudson" in i for i in issues)


def test_names_checked_word_by_word():
    art = "Sam Altman 的 OpenAI"
    assert not any("名字" in i for i in deterministic_issues("Sam Altman OpenAI 這件事，" + "字" * 200, art))


def test_compose_brief_names_column_and_fields():
    brief = fb_follow.compose_brief({"ticker": "3034", "title": "聯詠"}, "company")
    assert "fb_hook" in brief and "賺錢有道" in brief and "谷阿莫" in brief
    assert fb_follow.compose_brief({}, "unknown_mode") == ""


def test_writer_schema_never_rejects_article_over_missing_fb_fields():
    # FB 是附帶產物：模型漏寫 fb_* 不能讓整篇 Substack 作廢
    from substack_radar.composer import SubstackDraft
    d = SubstackDraft.model_validate({
        "title": "越拚命為何越失控", "subtitle": "一個高管教練的反直覺觀察與它的盲點",
        "seo_title": "t", "seo_description": "d", "tags": [], "body_markdown": "內文",
    })
    assert (d.fb_hook, d.fb_point, d.fb_figure, d.fb_post) == ("", "", "", "")


def test_sync_post_skips_when_writer_gave_no_fb_version(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(fb_follow, "DRAFTS_DIR", tmp_path)
    monkeypatch.setattr(fb_follow, "LEDGER_PATH", tmp_path / "l.json")
    folder = _mk(tmp_path, "2026-09-20", "x", datetime.now())
    empty = SimpleNamespace(fb_hook="", fb_point="", fb_figure="", fb_post="")
    assert fb_follow.post_with_draft(folder, empty) == "skipped"


def test_sync_post_blocked_when_substack_fact_audit_failed(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(fb_follow, "DRAFTS_DIR", tmp_path)
    monkeypatch.setattr(fb_follow, "LEDGER_PATH", tmp_path / "l.json")
    folder = _mk(tmp_path, "2026-09-20", "x", datetime.now(),
                 audit_warnings=["[品質迴圈未通過] E1：假出處"])
    piece = SimpleNamespace(fb_hook="鉤子", fb_point="重點", fb_figure="", fb_post="內文")
    assert fb_follow.post_with_draft(folder, piece) == "skipped"


def test_own_platform_names_are_not_flagged():
    assert not any("名字" in i for i in deterministic_issues("完整版在 Substack，" + "字" * 200, ARTICLE))


def test_sync_post_accepts_relative_folder(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(fb_follow, "DRAFTS_DIR", tmp_path)
    monkeypatch.setattr(fb_follow, "LEDGER_PATH", tmp_path / "l.json")
    folder = _mk(tmp_path, "2026-09-20", "x", datetime.now())
    monkeypatch.chdir(tmp_path)
    empty = SimpleNamespace(fb_hook="", fb_point="", fb_figure="", fb_post="")
    assert fb_follow.post_with_draft(Path("2026-09-20/x"), empty) == "skipped"
