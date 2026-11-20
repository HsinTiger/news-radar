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


def test_fb_show_check_accepts_names_without_initials_or_generic_words():
    """2026-11-20：節目是「Peter H. Diamandis」，FB 貼文寫「Peter Diamandis」被判沒點名，整篇沒發。"""
    from substack_radar.fb_follow import show_named
    assert show_named("Peter H. Diamandis", "Peter Diamandis 的節目裡，Figure 創辦人說")
    assert show_named("Joe Rogan Experience", "在 Joe Rogan 的節目上")
    assert not show_named("Joe Rogan Experience", "Rogan 說")          # 只寫姓不算點名
    assert not show_named("Lex Fridman Podcast", "一個播客節目裡")


def test_notes_drop_label_and_formula_question():
    from substack_radar.notes_desk import clean_note
    note = ("Figure 創辦人說，他希望六個月內機器人能進任何一間家做家事。\n\n"
            "我的判斷是：家用機器人離你家的距離，比他暗示的遠得多。什麼條件下我會改這個判斷？")
    out = clean_note(note)
    assert "我的判斷是" not in out and "什麼條件下" not in out
    assert "我目前傾向認為，家用機器人離你家的距離" in out


def test_company_picker_counts_taiwan_media_by_chinese_name():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("pick", Path(compose._REPO_ROOT) / "scripts" / "pick_company_candidate.py")
    pick = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pick)
    heat = pick.taiwan_heat([("AAPL", "Apple Inc."), ("HPQ", "HP Inc."), ("ZZZZ", "Unknown Co")],
                            "蘋果新機拉貨，台積電受惠。蘋果供應鏈。PHP 與 HPC 都不是公司名。")
    assert heat["AAPL"] == 2 and heat["HPQ"] == 0 and heat["ZZZZ"] == 0   # 短英文名不算，只數中文名


def test_audit_no_longer_demands_a_reply_question():
    """新口吻的結尾是交出一個讀者用得上的判斷方法；「文末沒有問句就警告」會把寫手推回舊公式。"""
    import inspect
    from substack_radar import composer
    assert "[缺少具體回信問題]" not in inspect.getsource(composer)


def test_research_brief_asks_for_the_taiwan_angle():
    from substack_radar import composer
    out = composer._build_research_brief_prompt(
        raw_title="Apple Inc.", raw_content="營收 4000 億美元", mode="company", topic_category="us_stocks",
        profile=composer.resolve_editorial_profile("company"), editor_brief="台灣讀者會問：「台灣讀者為什麼要在意蘋果？」")
    assert "台灣讀者為什麼要在意蘋果" in out and "這家公司跟台灣的關係" in out
    out = composer._build_research_brief_prompt(
        raw_title="Robotic Deep-Dive", raw_content="humanoid robots", mode="podcast", topic_category="ai",
        profile=composer.resolve_editorial_profile("podcast"), editor_brief="台灣讀者會問：「機器人熱，台灣供應鏈賺得到嗎？」")
    assert "這個問題在台灣的樣子" in out


def test_recent_titles_reach_the_editor_so_hot_topics_are_not_repeated(monkeypatch, tmp_path):
    from datetime import date, timedelta
    led = tmp_path / "data" / "substack_drafts"
    led.mkdir(parents=True)
    today = date.today()
    (led / ".daily_published.json").write_text(json.dumps({
        (today - timedelta(days=4)).isoformat(): {"title": "黃仁勳辭職 收盤只跌 4%｜吹牛免稅", "post_url": "u1"},
        today.isoformat(): {"title": "黃仁勳回不去？晶片調查卡住了｜吹牛免稅", "post_url": "u2"},
        (today - timedelta(days=30)).isoformat(): {"title": "很久以前的一篇", "post_url": "u3"},
        (today - timedelta(days=1)).isoformat(): {"title": "沒發出去的", "post_url": None},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(compose, "_REPO_ROOT", tmp_path)
    block = compose._recent_block()
    assert "黃仁勳辭職" in block and "黃仁勳回不去" in block
    assert "很久以前" not in block and "沒發出去" not in block


def test_reader_question_mandate_rules_out_stock_tips():
    assert "不給個股買賣建議" in compose._reader_question_mandate("輝達換人，台積電的訂單會不會受影響？")


def test_good_subheads_are_not_rewritten():
    """模型把合格的小標改寫成跟內文矛盾的說法，不能換進去；只換有毛病的。"""
    body = ("開場。\n\n## 他說中國在表演，卻把中國當頭號對手\n\n這兩段話不一定衝突。\n\n"
            "## 節目裡的其他線索\n\n內文二。\n")
    new, n = headline_desk.apply_subheads(body, ["中國只是在表演嗎？他前後的說法並不一致", "便宜了，大家就會換嗎？"])
    assert n == 1 and "## 他說中國在表演，卻把中國當頭號對手" in new and "## 便宜了，大家就會換嗎？" in new


def test_simpler_titles_win_within_the_assigned_arm():
    """Shulman et al. 2024（Science Advances，三萬多組標題實驗）：用字簡單的標題點擊較多。"""
    assert reader_signals.title_complexity("悖論的理性：合作是極致的自私") > reader_signals.title_complexity("醒來就欠世界八小時")
    assert reader_signals.title_complexity("AI 自己改工具") == 0                 # AI 是常見字
    body = "家用機器人的時程、成功率 56%、保險。"
    reply = json.dumps({"titles": ["機器人家務的範式轉移", "機器人進家門還要多久", "家務機器人敘事的本質",
                                   "保險卡住了機器人", "機器人進家，代價誰付", "成功率 56% 的家務機器人"],
                        "best": 1, "key_points": [], "note": "", "subheads": []}, ensure_ascii=False)
    out = headline_desk.polish(title="家用機器人進家門", subtitle="", body=body, column="吹牛免稅",
                               chat=lambda p: reply, arm="neutral")
    assert out["title"] == "機器人進家門還要多久"                               # 總編輯排第一的「範式轉移」比較難讀


def test_editor_pick_prefers_what_readers_would_forward(monkeypatch):
    rows = [("a", "A", "x", "o"), ("b", "B", "y", "o")]
    reply = {"scores": [
        {"n": 1, "tw_question": "台積電的訂單會受影響嗎？", "tw_score": 7, "score": 7, "share_to": "", "share_score": 9},
        {"n": 2, "tw_question": "房貸利率會跟著漲嗎？", "tw_score": 7, "score": 6, "share_to": "正在考慮買房的同事", "share_score": 8}]}
    monkeypatch.setattr(quality_loop, "_oss_chat", lambda role, prompt, timeout: json.dumps(reply, ensure_ascii=False))
    monkeypatch.setattr(compose, "_buzz_block", lambda: "")
    assert compose._editor_pick(rows) == rows[1]          # 寫不出會轉給誰，轉傳分數不算數
    assert compose.LAST_PICK_META["by_id"]["b"]["share_to"] == "正在考慮買房的同事"


def test_long_opening_passes_when_the_author_states_a_view_early():
    """新口吻：讀者的問題 → 主角帶著答案登場 → 作者傾向，開場常超過 12 句。前 12 句有表態就放寬到 16 句。"""
    from substack_radar import aeo_gate
    filler = "".join(f"這是開場第 {i} 句，講讀者的處境和主角的選擇。" for i in range(1, 9))
    lean = "我目前傾向認為，它離你家的距離比他暗示的遠得多。"
    more = "".join(f"這是補充的第 {i} 句，說明這個判斷成立的條件。" for i in range(1, 7))
    md = f"# 標題\n\n{filler}{lean}{more}\n\n## 第一個小標\n\n內文。"
    assert not [i for i in aeo_gate.check(md) if i.rule.startswith("A1 ")]            # 15 句、有表態：過
    md_no = f"# 標題\n\n{filler}{more}{filler[:40]}。\n\n## 第一個小標\n\n內文。"
    assert [i for i in aeo_gate.check(md_no) if i.rule.startswith("A1 ")]             # 沒表態又長：擋
    md_long = f"# 標題\n\n{filler}{lean}{more}{more}\n\n## 第一個小標\n\n內文。"
    assert [i for i in aeo_gate.check(md_long) if i.rule.startswith("A1 ")]           # 21 句：再長也擋


def test_research_shortfall_has_its_own_exit_code_and_the_weekly_job_retries_once():
    """11/14 寶僑：調研只找到 3 個來源，跟「草稿已建好」共用 exit 6，排程不敢重試，整天沒稿。"""
    import inspect
    from pathlib import Path
    assert compose.EXIT_RESEARCH_SHORT == 11 and "return EXIT_RESEARCH_SHORT" in inspect.getsource(compose)
    worker = (Path(compose._REPO_ROOT) / "scripts" / "substack_editorial_worker.sh").read_text(encoding="utf-8")
    block = worker.split('if [ "$COMPOSE_EXIT" -eq 11 ]; then', 1)[1].split("fi\nfi", 1)[0]
    assert "pick_company_candidate.py" in block and block.count("compose.py company") == 1   # 只重試一次
