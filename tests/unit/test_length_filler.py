"""2026-11-22 WO-4：略超字數走小修（不整篇重寫）；「其實」自動刪到剩一次。"""
from types import SimpleNamespace

from substack_radar import quality_loop as q
from substack_radar.composer import _count_chinese_chars, autofix_fillers
from tests.unit.test_fact_audit import DRAFT, _ledger_loop

SENT = "這是一段沒有問題的敘述句子內容。"          # 16 字


def _article(n_chars):
    reps = n_chars // _count_chinese_chars(SENT)
    return SENT * reps


# ---------- 字數 ----------
def test_length_in_range_has_no_item():
    assert q.length_feedback(_article(3500), (3000, 4000)) == []


def test_slightly_long_is_a_patch_not_a_rewrite():
    art = _article(4352)
    items = q.length_feedback(art, (3000, 4000))
    assert len(items) == 1
    it = items[0]
    assert it["結構"] is False
    assert "4000" in it["問題"]
    assert "刪" in it["建議改法"]


def test_far_too_long_or_short_is_structural():
    long_items = q.length_feedback(_article(5200), (3000, 4000))
    short_items = q.length_feedback(_article(2400), (3000, 4000))
    assert len(long_items) == 1 and long_items[0]["結構"] is True
    assert len(short_items) == 1 and short_items[0]["結構"] is True


def test_slightly_long_goes_to_patch_round(monkeypatch):
    target = "這是第 29 段用來湊篇幅、而且沒有任何問題的敘述句子。"
    assert target in DRAFT
    monkeypatch.setattr(q, "length_feedback",
                        lambda art, rng: [{"問題": "篇幅略長：上限 4000 字", "原句": "（全文）",
                                           "建議改法": "整句刪掉約 300 字", "結構": False}]
                        if target in art else [])
    import json
    reply = lambda p: json.dumps({"patches": [{"原句": target, "改成": ""}]}, ensure_ascii=False)  # noqa: E731
    (final, report, rewrites), calls = _ledger_loop(monkeypatch, [], reply)
    assert target not in final
    assert rewrites == 0 and report.patches >= 1
    assert "只修這幾句" in calls["writer"][0] and "篇幅略長" in calls["writer"][0]


# ---------- 其實 ----------
def _d(body):
    return SimpleNamespace(title="標題其實不動", subtitle="副標其實不動", body_markdown=body)


def test_filler_keeps_only_first_prose_occurrence():
    d = _d("其實甲很好。其實乙也好。後來其實丙更好。")
    fixes = autofix_fillers(d)
    assert fixes
    assert d.body_markdown.count("其實") == 1 and d.body_markdown.startswith("其實甲")
    assert d.title == "標題其實不動" and d.subtitle == "副標其實不動"


def test_filler_skips_quotes_blockquotes_and_source_section():
    body = ("他說「其實我不知道」。其實甲。其實乙。\n\n"
            "> 其實這是原話\n\n"
            "正文結束。\n\n---\n\n取材：其實這行不能動。")
    d = _d(body)
    autofix_fillers(d)
    out = d.body_markdown
    assert "「其實我不知道」" in out
    assert "> 其實這是原話" in out
    assert "取材：其實這行不能動。" in out
    assert "其實甲" in out and "其實乙" not in out and "乙。" in out


def test_filler_no_double_punctuation_and_idempotent():
    d = _d("其實甲。乙說，其實，丙不對。")
    autofix_fillers(d)
    out = d.body_markdown
    assert "，，" not in out and "其實甲" in out and out.count("其實") == 1
    again = out
    assert autofix_fillers(d) == [] and d.body_markdown == again
