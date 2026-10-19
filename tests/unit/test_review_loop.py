"""2026-10-18 信哥：審稿模型逐條給意見 → 退回寫手重寫，最多 3 次；還不過才算失敗。"""
from substack_radar import quality_loop as q

V = q.Violation(kind="E1 具名來源不在取材清單", detail="歸屬給「公司公布的數據」", fix_hint="原句：根據公司公布…")
ARTICLE = "# t\n\n" + "內文" * 300


def _setup(monkeypatch, *, gate_seq, critiques, rewrites):
    gates, crits, calls = list(gate_seq), list(critiques), {"rewrite_prompts": []}
    monkeypatch.setattr(q, "evaluate", lambda article, **k: gates.pop(0) if gates else [])
    monkeypatch.setattr(q, "_run_agy", lambda prompt, model, t: crits.pop(0) if crits else '{"items": []}')

    def writer(prompt, t):
        calls["rewrite_prompts"].append(prompt)
        return f"<<<ARTICLE>>>\n{rewrites.pop(0)}\n<<<END>>>", "閘道 writer"

    monkeypatch.setattr(q, "run_writer_text", writer)
    return calls


def test_feedback_goes_back_to_the_writer_until_clean(monkeypatch):
    calls = _setup(monkeypatch, gate_seq=[[V], []],
                   critiques=['{"items":[{"問題":"來源寫法","原句":"根據公司公布…","建議改法":"改成「依財報」"}]}',
                              '{"items": []}'],
                   rewrites=[ARTICLE + "改好"])
    out, report, n = q.run_review_loop(ARTICLE, fact_block="facts", auditor="a", eval_kwargs={})
    assert report.passed and n == 1 and out.endswith("改好")
    p = calls["rewrite_prompts"][0]
    assert "你是這篇文章的作者" in p and "改成「依財報」" in p and "E1 具名來源不在取材清單" in p


def test_three_rewrites_then_fail(monkeypatch):
    calls = _setup(monkeypatch, gate_seq=[[V], [V], [V], [V]], critiques=[],
                   rewrites=[ARTICLE + "1", ARTICLE + "2", ARTICLE + "3"])
    out, report, n = q.run_review_loop(ARTICLE, fact_block="", auditor="a", eval_kwargs={}, max_attempts=3)
    assert n == 3 and not report.passed and report.remaining == [V] and len(calls["rewrite_prompts"]) == 3


def test_auditor_only_comments_still_trigger_a_rewrite(monkeypatch):
    """程式檢查全過，但審稿抓到事實錯誤 → 也要退稿。"""
    calls = _setup(monkeypatch, gate_seq=[[], []],
                   critiques=['{"items":[{"問題":"分母算錯","原句":"吃掉四成毛利","建議改法":"是對營收的四成"}]}', '{"items": []}'],
                   rewrites=[ARTICLE + "修正"])
    out, report, n = q.run_review_loop(ARTICLE, fact_block="", auditor="a", eval_kwargs={})
    assert report.passed and n == 1 and "分母算錯" in calls["rewrite_prompts"][0]


def test_auditor_down_still_uses_gate_feedback(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("agy 429")
    calls = _setup(monkeypatch, gate_seq=[[V], []], critiques=[], rewrites=[ARTICLE + "x"])
    monkeypatch.setattr(q, "_run_agy", boom)
    out, report, n = q.run_review_loop(ARTICLE, fact_block="", auditor="a", eval_kwargs={})
    assert report.passed and n == 1 and "E1" in calls["rewrite_prompts"][0]


def test_too_short_article_is_sent_back_to_expand_with_more_source(monkeypatch):
    """2026-10-19：今天的吹牛免稅只有約 1100 字，只剩結論沒有故事 → 退稿補篇幅。"""
    short = "# t\n\n" + "短" * 500
    long_enough = "# t\n\n" + "故事" * 1600
    calls = _setup(monkeypatch, gate_seq=[[], []], critiques=['{"items": []}', '{"items": []}'], rewrites=[long_enough])
    out, report, n = q.run_review_loop(short, fact_block="逐字稿" * 5000, auditor="a", eval_kwargs={},
                                       word_range=(3000, 4000))
    assert n == 1 and report.passed and out == long_enough
    p = calls["rewrite_prompts"][0]
    assert "篇幅不足" in p and "起承轉合" in p and p.count("逐字稿") > 3000     # 補篇幅時給更多原始素材
