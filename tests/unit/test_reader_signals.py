"""2026-11-20 讀者數據迴圈：選題加「台灣讀者有沒有感」、標題改成實驗、每週報表算去趨勢成績。

主編問「是不是題材不夠爆」。查研究、拿 116 篇對照後：只靠模型判斷標題好壞，準度接近丟銅板
（LOLA, Marketing Science 2025）；我們的數據裡，負面／張力字的標題比同期多 4.5% 讀者、冒號少 7.3%，
問句和「你」看不出差別。所以選題要用台灣熱度與讀者真實成績校準，標題張力交給實驗。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from substack_radar import compose, headline_desk, reader_signals
from substack_radar import quality_loop

TW = timezone(timedelta(hours=8))


def test_trends_rss_and_ptt_pages_are_parsed_without_noise():
    xml = ("<rss><channel><item><title>台股 斷頭</title><ht:news_item_title><![CDATA[融資維持率創新低 520戶遭平倉]]>"
           "</ht:news_item_title></item><item><title>東北季風</title></item></channel></rss>")
    assert reader_signals.parse_trends_rss(xml) == ["台股 斷頭：融資維持率創新低 520戶遭平倉", "東北季風"]
    page = ('<div class="title"> <a href="/bbs/Stock/M.1.html">[公告] 板規</a></div>'
            '<div class="title"> <a href="/bbs/Stock/M.2.html">[新聞] 輝達財報優於預期</a></div>'
            '<div class="title"> <a href="/bbs/Stock/M.3.html">Re: [新聞] 輝達財報優於預期</a></div>'
            '<div class="title"> <a href="/bbs/home-sale/M.4.html">[賣/台北/北投] 新北投2房</a></div>')
    assert reader_signals.parse_ptt_index(page) == ["[新聞] 輝達財報優於預期"]


def test_buzz_survives_a_dead_source(tmp_path, monkeypatch):
    monkeypatch.setattr(reader_signals, "BUZZ_DIR", tmp_path)
    monkeypatch.setattr(reader_signals, "_tw_media_titles", lambda: ["中央社｜央行理監事會"])

    def fetch(url, cookies=None):
        if "trends" in url:
            raise OSError("down")
        return '<div class="title"> <a href="/x">[新聞] 美光擴產</a></div>'
    items = reader_signals.taiwan_buzz(fetch=fetch, refresh=True)
    assert "中央社｜央行理監事會" in items and any("美光擴產" in x for x in items)
    assert not any(x.startswith("Google") for x in items)


def test_editor_pick_requires_a_taiwan_reader_question(monkeypatch):
    rows = [("id-rogan", "Joe Rogan #2583", "long talk", "other"),
            ("id-robot", "Robotic Deep-Dive", "humanoid robots", "ai"),
            ("id-ngo", "NGO workers in India", "education policy", "other")]
    reply = {"scores": [
        {"n": 1, "tw_question": "", "tw_score": 2, "score": 9, "reader_takeaway": "好聽但跟台灣無關"},
        {"n": 2, "tw_question": "機器人熱，台灣供應鏈真的賺得到嗎？", "tw_score": 8, "score": 7, "reader_takeaway": "看懂量產門檻"},
        {"n": 3, "tw_question": "", "tw_score": 1, "score": 6, "reader_takeaway": "冷知識"}]}
    monkeypatch.setattr(quality_loop, "_oss_chat", lambda role, prompt, timeout: json.dumps(reply, ensure_ascii=False))
    monkeypatch.setattr(compose, "_buzz_block", lambda: "")
    assert compose._editor_pick(rows) == rows[1]
    assert compose.LAST_PICK_META["gate"] == "passed"
    assert compose.LAST_PICK_META["by_id"]["id-robot"]["tw_question"].startswith("機器人熱")


def test_editor_pick_falls_back_when_nothing_is_taiwan_relevant(monkeypatch):
    rows = [("a", "A", "x", "o"), ("b", "B", "y", "o")]
    reply = {"scores": [{"n": 1, "tw_question": "", "tw_score": 3, "score": 7},
                        {"n": 2, "tw_question": "", "tw_score": 2, "score": 5}]}
    monkeypatch.setattr(quality_loop, "_oss_chat", lambda role, prompt, timeout: json.dumps(reply))
    monkeypatch.setattr(compose, "_buzz_block", lambda: "")
    assert compose._editor_pick(rows) == rows[0]                  # 不能害整天沒稿：退回只看「能帶走什麼」
    assert compose.LAST_PICK_META["gate"] == "none_passed"


def test_editor_prompt_carries_buzz_and_reader_calibration(monkeypatch, tmp_path):
    cal = tmp_path / "cal.json"
    cal.write_text(json.dumps({"top": [{"title": "醒來就欠世界八小時", "res": 0.2}],
                               "bottom": [{"title": "意識也許沒那麼難", "res": -0.17}]}, ensure_ascii=False))
    monkeypatch.setattr(reader_signals, "CALIBRATION_PATH", cal)
    monkeypatch.setattr(compose, "_buzz_block", lambda: "【台灣這兩天在討論什麼】\n- PTT股票板｜[新聞] 台股斷頭\n\n")
    seen = {}

    def chat(role, prompt, timeout):
        seen["p"] = prompt
        return "{}"
    monkeypatch.setattr(quality_loop, "_oss_chat", chat)
    compose._editor_pick([("a", "A", "x", "o"), ("b", "B", "y", "o")])
    assert "台股斷頭" in seen["p"] and "醒來就欠世界八小時（+20%）" in seen["p"] and "tw_question" in seen["p"]


def test_title_styles_and_clickbait():
    assert reader_signals.title_style("黃仁勳回不去？晶片調查卡住了") == "tension"
    assert reader_signals.title_style("六個月進你家？成功率還不到六成") == "neutral"
    assert reader_signals.title_style("前輩的判斷該打折聽嗎") == "neutral"      # 「判斷」不是張力字
    assert reader_signals.is_clickbait("震驚！99% 的人都不知道") and not reader_signals.is_clickbait("高盛說沒事，卻叫你買保險")


def test_title_arm_is_stable_and_learns(tmp_path):
    empty = tmp_path / "none.json"
    assert reader_signals.title_arm("同一篇", path=empty) == reader_signals.title_arm("同一篇", path=empty)
    picks = {reader_signals.title_arm(f"s{i}", path=empty) for i in range(40)}
    assert picks == {"tension", "neutral"}                       # 沒資料時兩組都會抽到
    data = tmp_path / "arms.json"
    data.write_text(json.dumps({"arms": {"tension": {"n": 30, "mean": 0.10}, "neutral": {"n": 30, "mean": -0.08}}}))
    wins = sum(reader_signals.title_arm(f"s{i}", path=data) == "tension" for i in range(200))
    assert wins >= 190                                           # 證據夠了就幾乎都用贏的那組


def test_headline_desk_follows_the_assigned_arm_and_blocks_clickbait():
    body = "Figure 創辦人希望六個月內讓機器人進家門，但在 30 間住家的成功率只有 56%，保險也還沒有著落。"
    reply = json.dumps({"titles": ["震驚！機器人六個月進家門", "機器人進家門卡在保險", "六個月進家，代價誰付",
                                   "六個月進家門？成功率 56%", "30 間住家測出的機器人", "機器人進家門還要多久"],
                        "best": 1, "key_points": [], "note": "", "subheads": []}, ensure_ascii=False)
    chat = lambda p: reply
    neutral = headline_desk.polish(title="機器人六個月進家門", subtitle="", body=body, column="吹牛免稅",
                                   chat=chat, arm="neutral")
    assert neutral["title"] == "六個月進家門？成功率 56%" and neutral["title_style"] == "neutral"
    tension = headline_desk.polish(title="機器人六個月進家門", subtitle="", body=body, column="吹牛免稅",
                                   chat=chat, arm="tension")
    assert tension["title"] == "機器人進家門卡在保險" and tension["title_style"] == "tension"   # 震驚那個被擋


def test_weekly_report_detrends_and_skips_unsettled_posts():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("ssr", Path(compose._REPO_ROOT) / "scripts" / "substack_stats_report.py")
    ssr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ssr)
    now = datetime(2026, 11, 20, 12, tzinfo=TW)
    rows = [{"when": now - timedelta(days=30 - i), "sent": 158, "opened": 30, "title": f"t{i}"} for i in range(20)]
    rows[10]["opened"] = 36                                       # 這篇比同期多兩成
    rows.append({"when": now - timedelta(days=1), "sent": 158, "opened": 5, "title": "太新"})
    rows.append({"when": now - timedelta(days=200), "sent": 20, "opened": 15, "title": "名單太小"})
    scored = {r["title"]: r["res"] for r in ssr.detrend(rows, now=now)}
    assert abs(scored["t10"] - 0.2) < 1e-9 and scored["t3"] == 0
    assert "太新" not in scored and "名單太小" not in scored
