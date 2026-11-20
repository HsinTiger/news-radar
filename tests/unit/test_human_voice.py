"""2026-11-20：主編拿一篇他認為的好範本對照，說我們四個專欄「沒有溫度、很知識化、看不到人的思考過程」。

量出來的差距（範本 vs 11/17–11/19 三篇）：查證口吻每千字 0 vs 4–5 處、平均段長 70 vs 96–108 字、
舉例情境 4 vs 0–3 個、小標問句 2/5 vs 0/7。根因不在模型，在我們給寫手的規則：
「沒有的證據就寫查不到」「單一來源要說明限制」「反方寫成觀測指標」「最後留一個回信問題」。
"""
from __future__ import annotations

from substack_radar import composer, headline_desk
from substack_radar.quality_loop import apply_patches
from substack_radar.style_gate import style_feedback
from src.locale_tw import to_traditional


def _system(mode: str, monkeypatch, column: str | None = None) -> str:
    if column:
        monkeypatch.setenv("SUBSTACK_COLUMN", column)
    else:
        monkeypatch.delenv("SUBSTACK_COLUMN", raising=False)
    profile = composer.resolve_editorial_profile(mode, override="weekly" if column == "hsin" else None)
    return composer._build_system_instruction(profile)


def test_every_column_gets_the_thinking_habits_and_no_benchmark_name(monkeypatch):
    for mode, column in (("podcast", None), ("podcast", "ai"), ("company", None), ("morning", "hsin")):
        system = _system(mode, monkeypatch, column)
        assert "曼報" not in system and "Manny" not in system, (mode, column)
        assert "那句「可是……」" in system and "一把尺" in system and "讀者心裡那個問題" in system
        # 這兩句會讓寫手在正文記帳，已經拿掉
        assert "沒有的證據就寫「查不到」" not in system
        assert "「這一項我查不到可靠數字，所以下面的推論只到這裡為止」比任何修辭" not in system
        # 上游框架副本裡叫寫手「明寫查不到」的句子，載入時改寫掉
        assert "明寫「這裡查不到」" not in system and "查不到就寫查不到" not in system
        assert "counter-case-construction" not in system


def test_spec_style_frameworks_are_not_fed_to_the_prose_writer(monkeypatch):
    company = _system("company", monkeypatch)
    podcast = _system("podcast", monkeypatch)
    for system in (company, podcast):
        assert "〔名字〕：〔條件〕" not in system          # counter-case 規格塊
        assert "對接 BTC repo" not in system              # capital-allocation／cycle
    assert "四段式拆解" in company and "壞消息定價了沒" in company


def test_earnings_checklist_only_reaches_company_articles(monkeypatch):
    from tests.unit.test_substack_research_writer_contract import _brief, _sources

    def deep(mode):
        profile = composer.resolve_editorial_profile(mode)
        return composer._build_user_prompt(raw_title="x", raw_content="y", mode=mode, topic_category="ai_model",
                                           editorial_note="", profile=profile,
                                           research_brief=_brief(), research_sources=_sources())

    monkeypatch.delenv("SUBSTACK_COLUMN", raising=False)
    assert "財報拆解檢查清單" not in deep("podcast")
    assert "財報拆解檢查清單" in deep("company")
    assert "最後留一個本文特有的具體回信問題" not in deep("podcast")
    assert "結尾交出一把尺" in deep("podcast")


HEDGY = (
    "前陣子看到一段訪談，講到工具鎖定。"
    "這是他本人的陳述，訪談裡沒有外部數據可以核對。"
    "這是我的推論，訪談沒有直接這樣說。"
    "這一項我查不到可靠的數字，所以下面的推論只到這裡為止。"
    "這是 Notion 自己公佈的數字，沒有第三方核對過。"
    "這兩個數字我查不到，所以這條反方目前只能停在條件。"
)


def test_hedge_sentences_beyond_two_become_patchable_items():
    items = [i for i in style_feedback(HEDGY) if i["問題"].startswith("查證口吻")]
    assert len(items) >= 2
    assert all(i["原句"] in HEDGY for i in items)          # 逐句點名，小修才換得到
    calm = "前陣子看到一段訪談。公司說今年會再降價。如果成本真的每年降一半，毛利會回來。"
    assert not [i for i in style_feedback(calm) if i["問題"].startswith("查證口吻")]


def test_number_dense_sentence_is_flagged_but_years_do_not_count():
    dense = "供應商銷售年增 9.01%、季增 34.96%，淨利年減 12.43%，淨利率下滑至 6.23%。"
    assert any("個數字" in i["問題"] for i in style_feedback(dense))
    fine = "Figma 2026 年第二季營收年增 48%，但毛利只年增 40%，毛利率少了 5 個百分點。"
    assert not any("個數字" in i["問題"] for i in style_feedback(fine))


def test_patch_cannot_turn_a_wrong_sentence_into_a_hedge():
    article = "他說 2012 年用兩張顯示卡訓練了一週。後面還有別的句子。"
    out, applied, skipped = apply_patches(article, [{
        "原句": "他說 2012 年用兩張顯示卡訓練了一週。",
        "改成": "影片說 2012 年用兩張顯示卡訓練，但尚未獨立核實。",
    }], allowed_numbers=set())
    assert applied == 0 and any("查證口吻" in s for s in skipped)
    out, applied, _ = apply_patches(article, [{
        "原句": "他說 2012 年用兩張顯示卡訓練了一週。",
        "改成": "2012 年，研究團隊用兩張顯示卡訓練了一週。",
    }], allowed_numbers=set())
    assert applied == 1


def test_traditional_text_keeps_zhi_and_tai():
    assert to_traditional("他不是只在談 AI，工作平台在台灣") == "他不是只在談 AI，工作平台在台灣"
    assert to_traditional("紀律是只買第一") == "紀律是只買第一"
    assert to_traditional("这是简体，说明发展") == "這是簡體，說明發展"


def test_subheads_reject_template_chapters_and_forced_links():
    body = "## 舊的\n\n內容"
    for bad in ("最強反方怎麼說", "研究包查不到的部分", "接下來盯哪三個訊號", "那設局說到底多薄", "所以我的判斷是什麼"):
        assert not headline_desk.subhead_ok(bad, body), bad
    assert headline_desk.subhead_ok("便宜了，大家就會換嗎？", body)


def test_key_points_drop_hedge_bullets():
    body = "2012 年兩張 GTX 580 訓練約一週。客戶不換的關鍵是程式碼。"
    pts = headline_desk.grounded_points(
        ["2012 年兩張 GTX 580 訓練約一週，但尚未獨立核實。", "客戶不換的關鍵是程式碼，不是硬體。"], body)
    assert pts == ["客戶不換的關鍵是程式碼，不是硬體。"]


def test_copied_example_formula_and_show_metadata_are_flagged():
    """2026-11-20 試寫：契約例句被照抄成固定收尾；節目長度、導覽句也還留在稿子裡。"""
    txt = ("主持人在 Every 的 YouTube 節目裡（這集長 31 分鐘）提到一個做法。以下我一段一段拆。"
           "以後再遇到這種說法，我會先問兩件事。")
    problems = [i["問題"] for i in style_feedback(txt)]
    assert any("我會先問兩件事" in p for p in problems)
    assert any("節目長度" in p for p in problems)
    assert any("先報架構" in p for p in problems)
    assert not style_feedback("他在節目裡談了三十分鐘的供應鏈，最後只講了一個數字。")


def test_voice_examples_warn_against_copying():
    from pathlib import Path
    voice = (Path(composer.CONFIG_DIR) / "editorial_voice.md").read_text(encoding="utf-8")
    assert "不要照抄句子或句型" in voice
    assert "先問兩件事：誰得先付錢" not in voice      # 被照抄過的那句例句已換掉


def test_contract_jargon_ruler_does_not_reach_articles():
    assert any("一把尺" in i["問題"] for i in style_feedback("這把尺成立的前提，是基線和策略分開看。"))
    assert not style_feedback("他用一把尺量了桌子。")
    assert any("節目長度" in i["問題"] for i in style_feedback("前陣子看到一支約 55 分鐘的影片，講輝達。"))
