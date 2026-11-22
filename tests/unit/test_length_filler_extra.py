"""WO-4 補充測試：字數邊界、小修合併／退回重寫、legacy 相容、autofix_fillers 邊界情況。"""
import json
from types import SimpleNamespace

from substack_radar import quality_loop as q
from substack_radar.composer import _count_chinese_chars, autofix_fillers
from tests.unit.test_fact_audit import DRAFT, _item, _ledger_loop

SENT = "這是一段沒有問題的敘述句子內容。"  # 16 字


def _article(n_chars: int) -> str:
    return SENT * (n_chars // _count_chinese_chars(SENT))


# ---------- length_feedback 邊界 ----------
def test_length_feedback_boundaries():
    assert q.length_feedback(_article(3500), None) == []
    # 剛好等於上限 hi → []
    exact_hi = "字" * 4000
    assert q.length_feedback(exact_hi, (3000, 4000)) == []
    # 略超：hi < n <= hi * 1.25（含恰好 1.25 倍 = 5000）
    just_over = "字" * 4342
    items = q.length_feedback(just_over, (3000, 4000))
    assert len(items) == 1
    it = items[0]
    assert it["結構"] is False
    assert "篇幅略長" in it["問題"] and "4342" in it["問題"] and "4000" in it["問題"]
    assert it["原句"] == "（全文）"
    assert "刪" in it["建議改法"] and "342" in it["建議改法"]

    at_125 = "字" * 5000
    items_125 = q.length_feedback(at_125, (3000, 4000))
    assert len(items_125) == 1 and items_125[0]["結構"] is False

    # 超過 1.25 倍 → 結構=True
    over_125 = "字" * 5001
    items_over = q.length_feedback(over_125, (3000, 4000))
    assert len(items_over) == 1 and items_over[0]["結構"] is True
    assert "篇幅過長" in items_over[0]["問題"]

    # 剛好 lo * 0.9 → []；低於 lo * 0.9 → 結構=True
    assert q.length_feedback("字" * 2700, (3000, 4000)) == []
    short_items = q.length_feedback("字" * 2699, (3000, 4000))
    assert len(short_items) == 1 and short_items[0]["結構"] is True
    assert "篇幅不足" in short_items[0]["問題"]


# ---------- _run_review_loop_ledger 行為 ----------
def test_missing_structural_key_defaults_to_full_rewrite(monkeypatch):
    monkeypatch.setattr(
        q, "length_feedback",
        lambda art, rng: [] if "補寫" in art else [{"問題": "篇幅不足", "原句": "（全文）", "建議改法": "補"}],
    )
    reply = lambda p: f"<<<ARTICLE>>>\n{DRAFT}\n\n補寫一段。\n<<<END>>>"  # noqa: E731
    (final, report, rewrites), _ = _ledger_loop(monkeypatch, [], reply)
    assert rewrites == 1 and report.patches == 0 and "補寫一段" in final


def test_slightly_long_merges_with_audit_and_style_in_same_patch_round(monkeypatch):
    target_len = "這是第 29 段用來湊篇幅、而且沒有任何問題的敘述句子。"
    bad_fact = "節目裡提到 Polymarket 預測今年底前回任的機率六成以上。"
    fixed_fact = "節目裡提到 Polymarket 預測明年底前回任的機率有 30% 以上。"
    assert target_len in DRAFT and bad_fact in DRAFT

    monkeypatch.setattr(
        q, "length_feedback",
        lambda art, rng: [{"問題": "篇幅略長：目前約 4342 字，上限 4000 字", "原句": "（全文）",
                           "建議改法": "整句刪掉約 342 字", "結構": False}]
        if target_len in art else [],
    )
    monkeypatch.setattr(
        "substack_radar.style_gate.style_feedback",
        lambda art: [{"問題": "文風重複", "原句": bad_fact, "建議改法": "順一下"}] if bad_fact in art else [],
    )
    audit_item = _item(bad_fact, "a 30 plus percent chance that he'll return as CEO by the end of next year")
    reply = lambda p: json.dumps({"patches": [  # noqa: E731
        {"原句": target_len, "改成": ""},
        {"原句": bad_fact, "改成": fixed_fact},
    ]}, ensure_ascii=False)
    (final, report, rewrites), calls = _ledger_loop(monkeypatch, [audit_item], reply)
    assert rewrites == 0 and report.patches == 2 and report.passed
    assert target_len not in final and fixed_fact in final
    assert len(calls["writer"]) == 1
    prompt = calls["writer"][0]
    assert "篇幅略長" in prompt and "跟素材不符" in prompt and "文風重複" in prompt


def test_slightly_long_falls_back_to_rewrite_when_patch_applies_zero(monkeypatch):
    monkeypatch.setattr(
        q, "length_feedback",
        lambda art, rng: [] if "精簡版" in art else [
            {"問題": "篇幅略長：目前約 4300 字，上限 4000 字", "原句": "（全文）",
             "建議改法": "整句刪掉約 300 字", "結構": False}
        ],
    )

    def reply(p):
        if "只修這幾句" in p:
            return json.dumps({"patches": [{"原句": "文章裡找不到的句子", "改成": ""}]})
        return f"<<<ARTICLE>>>\n{DRAFT}\n\n精簡版\n<<<END>>>"

    (final, report, rewrites), calls = _ledger_loop(monkeypatch, [], reply)
    assert rewrites == 1 and report.patches == 0 and "精簡版" in final
    assert len(calls["writer"]) == 2


# ---------- _run_review_loop_legacy 相容 ----------
def test_legacy_loop_handles_structural_key(monkeypatch):
    monkeypatch.setenv("SUBSTACK_REVIEW_MODE", "legacy")
    monkeypatch.setattr(q, "evaluate", lambda a, **k: [])
    monkeypatch.setattr(q, "critique", lambda *a, **k: [])
    monkeypatch.setattr(
        q, "length_feedback",
        lambda art, rng: [] if "精簡" in art else [
            {"問題": "篇幅略長：目前約 4300 字，上限 4000 字", "原句": "（全文）",
             "建議改法": "整句刪掉約 300 字", "結構": False}
        ],
    )
    monkeypatch.setattr(q, "run_writer_text", lambda p, t: (f"<<<ARTICLE>>>\n{DRAFT}\n\n精簡\n<<<END>>>", "測試寫手"))
    final, report, rewrites = q.run_review_loop(DRAFT, fact_block="", auditor="x", eval_kwargs={})
    assert report.passed and rewrites == 1 and "精簡" in final


# ---------- autofix_fillers 邊界 ----------
def _d(body: str):
    return SimpleNamespace(title="標題其實不動", subtitle="副標其實不動", body_markdown=body)


def test_filler_skips_all_quote_types_inline_code_and_fences():
    body = (
        "他說「其實一」、又說『其實二』、還引述“其實三”。\n"
        "程式碼 `其實四` 不算。\n\n"
        "```\n其實五\n```\n\n"
        "## 其實六是小標（這才是正文第一次）\n"
        "## 其實七是第二個小標\n"
        "正文最後其實，這裡也要刪。"
    )
    d = _d(body)
    fixes = autofix_fillers(d)
    assert fixes == ["[自動修正:填充詞] 其實 ×2 刪除"]
    out = d.body_markdown
    assert "「其實一」" in out
    assert "『其實二』" in out
    assert "“其實三”" in out
    assert "`其實四`" in out
    assert "```\n其實五\n```" in out
    assert "## 其實六是小標（這才是正文第一次）" in out
    assert "## 七是第二個小標" in out
    assert "正文最後這裡也要刪。" in out
    assert autofix_fillers(d) == []


def test_filler_punctuation_cleanup_and_source_section():
    body = (
        "其實甲。\n"
        "乙說，其實。丙說。\n"
        "丁說。其實。戊說。\n"
        "己說，其實？庚說。\n"
        "辛說，其實！壬說。\n\n"
        "---\n\n"
        "取材：其實這段不能動，其實這句也不能動。\n"
        "---\n"
        "其實後面也不動。"
    )
    d = _d(body)
    fixes = autofix_fillers(d)
    assert fixes == ["[自動修正:填充詞] 其實 ×4 刪除"]
    out = d.body_markdown
    assert "乙說。丙說。" in out
    assert "丁說。戊說。" in out
    assert "己說？庚說。" in out
    assert "辛說！壬說。" in out
    assert "取材：其實這段不能動，其實這句也不能動。" in out
    assert "其實後面也不動。" in out
    for bad in ("，，", "。，", "，。", "。。", "，？", "，！"):
        assert bad not in out
    assert autofix_fillers(d) == []


def test_filler_empty_or_single_prose_returns_empty():
    assert autofix_fillers(_d("")) == []
    d = _d("正文只有一次其實。")
    assert autofix_fillers(d) == []
    assert d.body_markdown == "正文只有一次其實。"
