"""每天 07:30 自動發布一篇（2026-10-15）。守住：發哪一欄、拿乾淨 AI 痕跡、絕不重寄信。"""

import json
from datetime import date

import pytest

from substack_radar import daily_publish as dp


def test_weekdays_publish_podcast_weekends_publish_company():
    mon, fri, sat, sun = date(2026, 10, 12), date(2026, 10, 16), date(2026, 10, 17), date(2026, 10, 18)
    assert dp.plan_for(mon)[0] == (mon, dp.PODCAST)
    assert dp.plan_for(fri)[0] == (fri, dp.PODCAST)
    assert dp.plan_for(sat) == [(sat, dp.COMPANY), (sat, dp.PODCAST)]    # 公司分析沒寫成就發當天的 Podcast
    assert dp.plan_for(sun)[0] == (sun, dp.COMPANY)
    assert dp.plan_for(mon)[1] == (date(2026, 10, 11), dp.PODCAST)       # 平日備胎：前一天沒發的


def test_strip_ai_traces_removes_only_the_provenance_box():
    body = {"type": "doc", "content": [
        {"type": "captionedImage"},
        {"type": "blockquote", "content": [{"type": "paragraph", "content": [
            {"type": "text", "text": "🧠 產文路線：CCR · 模型 claude-opus-5｜稽核 Gemini"}]}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "正文第一段。"}]},
        {"type": "blockquote", "content": [{"type": "paragraph", "content": [
            {"type": "text", "text": "「把複雜世界寫成人話」"}]}]},
    ]}
    clean, removed = dp.strip_ai_traces(body)
    assert removed == 1
    assert [n["type"] for n in clean["content"]] == ["captionedImage", "paragraph", "blockquote"]
    assert not dp._has_ai_trace(clean)


def _draft(tmp_path, day, name, title, draft_id="111", warnings=()):
    folder = tmp_path / day / name
    folder.mkdir(parents=True)
    (folder / "metadata.json").write_text(json.dumps({
        "title": title, "substack_draft_id": draft_id, "created_at": f"{day}T06:20:00",
        "audit_warnings": list(warnings), "subtitle": "副標"}, ensure_ascii=False), encoding="utf-8")
    return folder


def test_candidates_filter_column_fact_failures_and_already_published(tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "DRAFTS_DIR", tmp_path)
    _draft(tmp_path, "2026-10-16", "a", "好文｜吹牛免稅", "1")
    _draft(tmp_path, "2026-10-16", "b", "手動投稿｜主編精選", "2")                    # 主編精選永遠不自動發
    _draft(tmp_path, "2026-10-16", "c", "數字有問題｜吹牛免稅", "3",
           warnings=["[品質迴圈未通過] E3：數字找不到"])
    _draft(tmp_path, "2026-10-16", "d", "沒推上 Substack｜吹牛免稅", "")
    _draft(tmp_path, "2026-10-16", "e", "昨天已發｜吹牛免稅", "5")
    ledger = {"2026-10-15": {"folder": "2026-10-16/e", "post_url": "https://x/p/e"}}
    got = [c.folder.name for c in dp.candidates(date(2026, 10, 16), dp.PODCAST, ledger)]
    assert got == ["a"]


def test_deleted_draft_counts_as_veto():
    class Api:
        def get_draft(self, _):
            raise Exception("APIError(code=404): Draft not found")
    cand = dp.Candidate(dp.DRAFTS_DIR / "x" / "y", {}, dp.PODCAST, "h", "9", "")
    assert dp.still_on_substack(Api(), cand) is False


def test_rule_fallback_prefers_fewest_warnings_then_newest(monkeypatch):
    import src.llm_brain as brain

    async def broken(**_k):
        raise RuntimeError("all backends down")
    monkeypatch.setattr(brain, "call_for_json", broken)
    a = dp.Candidate(dp.DRAFTS_DIR / "d" / "a", {}, dp.PODCAST, "a", "1", "2026-10-16T06:10", ["w1", "w2"])
    b = dp.Candidate(dp.DRAFTS_DIR / "d" / "b", {}, dp.PODCAST, "b", "2", "2026-10-16T06:20", ["w1"])
    c = dp.Candidate(dp.DRAFTS_DIR / "d" / "c", {}, dp.PODCAST, "c", "3", "2026-10-16T06:40", ["w1"])
    chosen, reason = dp.pick([a, b, c])
    assert chosen is c and "規則" in reason


class FakeApi:
    def __init__(self):
        self.calls = []
        self.body = {"type": "doc", "content": [
            {"type": "blockquote", "content": [{"type": "text", "text": "🧠 產文路線：模型 x"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "正文"}]}]}

    def get_draft(self, _id):
        return {"draft_body": json.dumps(self.body), "is_published": False}

    def put_draft(self, _id, draft_body):
        self.calls.append("put")
        self.body = json.loads(draft_body)

    def prepublish_draft(self, _id):
        self.calls.append("prepublish")

    def publish_draft(self, _id, send=True, share_automatically=False):
        self.calls.append(("publish", send))

    def get_published_posts(self, **_k):
        return {"posts": [{"id": 7, "slug": "today", "canonical_url": "https://hsin73.substack.com/p/today"}]}


def test_publish_strips_traces_sends_email_and_records(tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "LEDGER_PATH", tmp_path / ".daily_published.json")
    monkeypatch.setattr(dp, "DRAFTS_DIR", tmp_path)
    api, ledger = FakeApi(), {}
    cand = dp.Candidate(tmp_path / "2026-10-16" / "a", {"title": "t"}, dp.PODCAST, "t", "7", "")
    url = dp.publish(api, cand, ledger, "2026-10-16", "test")
    assert url == "https://hsin73.substack.com/p/today"
    assert api.calls == ["put", "prepublish", ("publish", True)]   # 先拿掉痕跡、寄信發布
    assert not dp._has_ai_trace(api.body)
    saved = json.loads((tmp_path / ".daily_published.json").read_text())
    assert saved["2026-10-16"]["post_url"] == url


def test_a_previous_attempt_is_never_resent(tmp_path, monkeypatch):
    """發布請求送出後沒收到回應：之後任何重跑都只查，不再寄一次信。"""
    monkeypatch.setattr(dp, "LEDGER_PATH", tmp_path / ".daily_published.json")
    api = FakeApi()
    ledger = {"2026-10-16": {"draft_id": "7", "publish_attempted_at": "2026-10-16T07:30:05"}}
    cand = dp.Candidate(tmp_path / "x", {}, dp.PODCAST, "t", "7", "")
    assert dp.publish(api, cand, ledger, "2026-10-16", "test") == "https://hsin73.substack.com/p/today"
    assert ("publish", True) not in api.calls and "prepublish" not in api.calls


def test_traces_that_survive_the_edit_block_publishing(tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "LEDGER_PATH", tmp_path / ".daily_published.json")
    api = FakeApi()
    api.put_draft = lambda _id, draft_body: None          # Substack 沒吃進修改
    cand = dp.Candidate(tmp_path / "x", {}, dp.PODCAST, "t", "7", "")
    with pytest.raises(RuntimeError):
        dp.publish(api, cand, {}, "2026-10-16", "test")
    assert "prepublish" not in api.calls
