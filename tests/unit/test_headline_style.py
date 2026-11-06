"""2026-11-06 總編輯審稿：標題台、30 秒重點、文風意見、選題節目輪替。"""
import json

from substack_radar import headline_desk
from substack_radar.compose import _show_key
from substack_radar.style_gate import style_feedback


def test_headline_desk_skips_bad_candidates_and_ungrounded_points():
    body = "Dan 說 AI agent 還在 2% 的早期階段。主持人 10% 時間做事、90% 時間調教 agent。"
    reply = json.dumps({"titles": ["AI 代理：還在 2% 階段", "你九成時間在調教代理", "x"], "best": 1,
                        "key_points": ["AI 代理還在 2% 的早期階段", "75% 的人放棄", "主持人 90% 時間在調教"]})
    out = headline_desk.polish(title="舊標題", subtitle="", body=body, column="智取未來", chat=lambda p: reply)
    assert out["title"] == "你九成時間在調教代理"          # best 有冒號被擋，改用下一個合格的
    assert out["key_points"] == ["AI 代理還在 2% 的早期階段", "主持人 90% 時間在調教"]   # 75% 正文沒有


def test_headline_desk_failure_keeps_original():
    def boom(p):
        raise RuntimeError("gateway down")
    assert headline_desk.polish(title="t", subtitle="", body="b", column="c", chat=boom) == \
        {"title": None, "key_points": []}


def test_style_feedback_flags_stock_phrases_and_untranslated_english():
    art = "## 笨錘子\n\n這段我認為是全集最扎實的部分，他說 agent 還在 bleeding edge 階段。\n\n最後留一個問題給你。"
    problems = " ".join(i["問題"] for i in style_feedback(art))
    assert "AI 套話" in problems and "bleeding edge" in problems and "小標像標籤" in problems


def test_show_key_groups_episodes_of_same_show():
    assert _show_key("Joe Rogan Experience #2577 - Gregory Forth") == \
        _show_key("Joe Rogan Experience #2575 - Sam Tallent")
    assert _show_key("All-In E200: Sacks") == "all-in"


def test_evidence_gate_accepts_primary_source_and_english_units():
    """2026-11-06 Anthropic 試跑：寫手引用節目本身、英文 $80 billion 寫成 800 億，三輪都被判無出處。"""
    from substack_radar.evidence_gate import check
    facts = "Hard Fork. Aaron Griffith: Anthropic booked a net loss of $80 billion. Operating loss was $693 million."
    ok = ("根據一集科技 Podcast 的對談，Anthropic 帳列淨損約 800 億美元。"
          "根據 Aaron 在節目裡的說法，本業只虧 6.93 億美元。")
    assert check(ok, sources=[], fact_block=facts) == []
    bad = "根據高盛分析師的報告，估值有 2 兆美元。"
    assert any(i.rule.startswith("E3") for i in check(bad, sources=[], fact_block=facts))


def test_evidence_pack_shows_late_transcript_and_research_sources():
    from substack_radar.quality_loop import evidence_pack
    transcript = "x" * 30000 + " late fact 63 billion"
    pack = evidence_pack(transcript, [{"publisher": "SEC", "title": "Anthropic S-1",
                                       "url": "https://sec.gov/a", "excerpt": "revenue 6.3"}])
    assert "late fact 63 billion" in pack and "https://sec.gov/a" in pack
