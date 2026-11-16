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
        {"title": None, "key_points": [], "note": "", "body": "b", "subheads_changed": 0}   # 正文原樣


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


# ---- 2026-11-16：信哥讀完黃仁勳那篇，說小標與內文生硬 ----

def test_process_talk_and_repeated_show_prefix_are_flagged():
    from substack_radar.style_gate import style_feedback

    stiff = ("開場。\n\n## 一個完整的小標在這裡\n\n"
             "在 Moonshots 裡，Alex 說了第一件事。在 Moonshots 裡，Imad 接話。"
             "在 Moonshots 裡，主持人追問。在 Moonshots 裡，Salim 反駁。\n\n"
             "這些細節我只能在節目逐字稿裡看到，沒有對應到提供的研究來源，所以這裡不採用。\n")
    problems = " ".join(i["問題"] for i in style_feedback(stiff))
    assert "查證過程寫進了文章" in problems and "逐字稿" in problems
    assert "出現 4 次" in problems

    natural = ("開場。\n\n## 一個完整的小標在這裡\n\n"
               "這集 Moonshots 一開始，Alex 先講了第一件事，Imad 接話，主持人再追問。\n\n"
               "這一點目前只有他自己這樣講，我沒查到別的報導。\n")
    assert not [i for i in style_feedback(natural) if "查證過程" in i["問題"] or "出現" in i["問題"]]


def test_subheads_are_swapped_only_when_safe():
    from substack_radar.headline_desk import apply_subheads, polish

    body = "開場提到 4%。\n\n## 「被設局」這條線，證據最薄\n\n內文一。\n\n## 節目裡的其他線索\n\n內文二。\n"
    new, n = apply_subheads(body, ["那是不是有人設局", "有人說他像國王一樣管公司"])
    assert n == 2 and "## 那是不是有人設局" in new and "內文一。" in new and "內文二。" in new

    # 數量對不上：整批不換
    assert apply_subheads(body, ["只有一個小標而已"]) == (body, 0)
    # 帶正文沒有的數字、帶冒號、報告用語：那一個保留原樣
    new, n = apply_subheads(body, ["股價大跌 37% 的真相", "綜合起來看這件事情"])
    assert n == 0 and new == body
    new, n = apply_subheads(body, ["設局：證據有多薄呢", "有人說他像國王一樣管公司"])
    assert n == 1 and "「被設局」這條線，證據最薄" in new

    out = polish(title="舊標題", subtitle="", body=body, column="吹牛免稅",
                 chat=lambda p: '{"titles": ["新標題在這裡"], "best": 1, "key_points": [], "note": "",'
                                ' "subheads": ["那是不是有人設局", "有人說他像國王一樣管公司"]}')
    assert out["subheads_changed"] == 2 and "## 那是不是有人設局" in out["body"]


def test_rewrite_uses_same_order_as_first_writer(monkeypatch):
    from substack_radar import quality_loop as q

    monkeypatch.setenv("SUBSTACK_COMPOSER_BACKEND", "claude_cli,antigravity_cli,gateway")
    assert q._writer_order() == ["claude", "agy", "gateway"]
    monkeypatch.setenv("SUBSTACK_COMPOSER_BACKEND", "")
    assert q._writer_order() == ["agy", "gateway", "claude"]

    monkeypatch.setenv("SUBSTACK_COMPOSER_BACKEND", "claude_cli,gateway")
    monkeypatch.setattr(q, "run_claude_cli", lambda p, t: (_ for _ in ()).throw(RuntimeError("額度用完")))
    monkeypatch.setattr(q, "_oss_chat", lambda m, p, t: "閘道稿")
    assert q.run_writer_text("x", 1) == ("閘道稿", "閘道 writer")     # Haiku 不行才換閘道
    monkeypatch.setattr(q, "run_claude_cli", lambda p, t: "Haiku 稿")
    assert q.run_writer_text("x", 1) == ("Haiku 稿", "Claude Haiku")
