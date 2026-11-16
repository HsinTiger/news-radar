"""對帳式審稿（2026-11-16）：模型只找「草稿跟素材矛盾」，證據由程式驗；有問題先小修那幾句。"""
import json

from substack_radar import fact_audit as fa
from substack_radar import quality_loop as q

MATERIAL = ("Polymarket is predicting something like a 30 plus percent chance that he'll return as CEO "
            "by the end of next year. Shares plunged more than 13% at the open and closed 4.3% down. "
            "主持人說，這家公司去年的雲端營收成長了 39%，整體營收只成長 12%。")
PAD = "".join(f"這是第 {n} 段用來湊篇幅、而且沒有任何問題的敘述句子。" for n in range(30))
DRAFT = ("節目裡提到 Polymarket 預測今年底前回任的機率六成以上。這家公司去年整體營收成長了 39%。\n\n"
         "## 這是一個夠長的完整小標\n\n第三句是原本就寫對的內容。" + PAD)


def _item(sentence, evidence, problem="期限寫錯"):
    return {"原句": sentence, "素材原文": evidence, "問題": problem, "建議改法": "照素材改"}


def test_only_items_with_real_evidence_survive():
    good = _item("節目裡提到 Polymarket 預測今年底前回任的機率六成以上。",
                 "a 30 plus percent chance that he'll return as CEO by the end of next year")
    fake_evidence = _item("這家公司去年整體營收成長了 39%。", "The company said total revenue grew 5% across all segments last year")
    not_in_draft = _item("草稿裡根本沒有這一句話存在。", "closed 4.3% down")
    absence = _item("第三句是原本就寫對的內容。", "Shares plunged more than 13% at the open", "素材中沒有提及這件事")
    no_evidence = _item("第三句是原本就寫對的內容。", "")
    kept, dropped = fa.verify([good, fake_evidence, not_in_draft, absence, no_evidence], DRAFT, MATERIAL)
    assert kept == [good]
    assert [why for _, why in dropped] == ["附的素材原文在素材裡找不到", "指的那句不在草稿裡",
                                         "講的是「素材沒有」，不是矛盾", "沒有附素材原文"]


def test_evidence_copied_with_small_slips_still_counts():
    slip = _item("這家公司去年整體營收成長了 39%。", "主持人說,這家公司去年的雲端營收成長了39%,整體營收只成長12%")
    assert fa.verify([slip], DRAFT, MATERIAL)[0] == [slip]


def test_two_models_run_together_and_one_failing_is_fine():
    a = _item("節目裡提到 Polymarket 預測今年底前回任的機率六成以上。", "by the end of next year")
    b = _item("這家公司去年整體營收成長了 39%。", "雲端營收成長了 39%，整體營收只成長 12%", "部門數字寫成整體")

    def chat(model, prompt):
        if model == "壞掉的":
            raise RuntimeError("閘道回空字串")
        return json.dumps({"items": [a, b] if model == "甲" else [dict(a)]}, ensure_ascii=False)

    kept, summary = fa.run(DRAFT, MATERIAL, models=["甲", "乙", "壞掉的"], chat=chat, deadline_s=5)
    assert sorted(summary["answered"]) == ["乙", "甲"] and "壞掉的" in summary["failed"]
    assert len(kept) == 2                                    # 同一句兩個模型都報，只算一次
    kept, summary = fa.run(DRAFT, MATERIAL, models=["壞掉的"], chat=chat, deadline_s=5)
    assert kept == [] and summary["answered"] == []          # 呼叫端要知道這次沒人對過帳
    # 主要的都沒回 → 備援查一次；有人回了就不叫備援
    kept, summary = fa.run(DRAFT, MATERIAL, models=["壞掉的"], chat=chat, deadline_s=5, fallback=["乙"])
    assert summary["answered"] == ["乙"] and len(kept) == 1
    _, summary = fa.run(DRAFT, MATERIAL, models=["甲"], chat=chat, deadline_s=5, fallback=["乙"])
    assert summary["answered"] == ["甲"]


def test_slow_model_does_not_hold_the_article(monkeypatch):
    import time
    a = _item("節目裡提到 Polymarket 預測今年底前回任的機率六成以上。", "by the end of next year")

    def chat(model, prompt):
        if model == "慢的":
            time.sleep(3)
        return json.dumps({"items": [a]}, ensure_ascii=False)

    t0 = time.time()
    kept, summary = fa.run(DRAFT, MATERIAL, models=["快的", "慢的"], chat=chat, deadline_s=30, grace_s=0, fallback=[])
    assert time.time() - t0 < 2.5 and summary["answered"] == ["快的"] and "慢的" in summary["failed"] and kept


def _ledger_loop(monkeypatch, audit_items, writer_reply, evaluate=lambda a, **k: []):
    calls = {"audit": 0, "writer": []}

    def fake_run(article, pack, **kw):
        calls["audit"] += 1
        calls.setdefault("writers", []).append(list(kw.get("writers") or []))
        return list(audit_items), {"answered": ["甲", "乙"], "failed": {}, "raw": len(audit_items), "dropped": 0}

    def fake_writer(prompt, timeout):
        calls["writer"].append(prompt)
        return writer_reply(prompt), "測試寫手"

    monkeypatch.delenv("SUBSTACK_REVIEW_MODE", raising=False)
    monkeypatch.setattr(fa, "run", fake_run)
    monkeypatch.setattr(q, "evaluate", evaluate)
    monkeypatch.setattr(q, "run_writer_text", fake_writer)
    out = q.run_review_loop(DRAFT, fact_block=MATERIAL, auditor="x", eval_kwargs={}, max_attempts=2)
    return out, calls


def test_clean_first_draft_passes_without_any_writer_call(monkeypatch):
    (final, report, rewrites), calls = _ledger_loop(monkeypatch, [], lambda p: "")
    assert report.passed and report.rounds == 1 and rewrites == 0 and report.patches == 0
    assert final == DRAFT and calls["writer"] == [] and report.auditors == ["甲", "乙"]


def test_contradiction_is_patched_in_place_and_not_reaudited(monkeypatch):
    bad = "節目裡提到 Polymarket 預測今年底前回任的機率六成以上。"
    fixed = "節目裡提到 Polymarket 預測明年底前回任的機率有 30% 以上。"
    item = _item(bad, "a 30 plus percent chance that he'll return as CEO by the end of next year")
    reply = lambda p: json.dumps({"patches": [{"原句": bad, "改成": fixed}]}, ensure_ascii=False)
    (final, report, rewrites), calls = _ledger_loop(monkeypatch, [item], reply)
    assert fixed in final and bad not in final
    assert final.replace(fixed, bad) == DRAFT                 # 其他地方一個字都沒動
    assert report.passed and rewrites == 0 and report.patches == 1 and report.rounds == 2
    assert calls["audit"] == 1 and len(calls["writer"]) == 1  # 模型只對帳一次，寫手只被叫一次
    assert "只修這幾句" in calls["writer"][0] and "素材原文" in calls["writer"][0]


def test_too_short_goes_to_full_rewrite(monkeypatch):
    monkeypatch.setattr(q, "length_feedback",
                        lambda art, rng: [] if "補寫" in art else [{"問題": "篇幅不足", "原句": "（全文）", "建議改法": "補"}])
    reply = lambda p: f"<<<ARTICLE>>>\n{DRAFT}\n\n補寫的一段。\n<<<END>>>"
    (final, report, rewrites), calls = _ledger_loop(monkeypatch, [], reply)
    assert rewrites == 1 and report.patches == 0 and "補寫的一段" in final and report.passed
    assert calls["audit"] == 1                                # 先補篇幅、再對帳：一篇只對一次
    assert calls["writers"][0] == ["測試寫手"]                  # 重寫的人也列進「不能當查核員」


def test_final_draft_is_audited_even_if_the_structural_rewrite_fails(monkeypatch):
    monkeypatch.setattr(q, "length_feedback",
                        lambda art, rng: [{"問題": "篇幅不足", "原句": "（全文）", "建議改法": "補"}])
    bad = "這家公司去年整體營收成長了 39%。"
    item = _item(bad, "雲端營收成長了 39%，整體營收只成長 12%", "部門數字寫成整體")
    (final, report, rewrites), calls = _ledger_loop(monkeypatch, [item], lambda p: "寫手沒有交稿")
    assert rewrites == 0 and final == DRAFT
    assert calls["audit"] == 1 and report.unresolved          # 重寫沒成功，定稿仍然對過帳、問題有報出來


def test_unfixed_contradiction_is_reported_not_hidden(monkeypatch):
    bad = "這家公司去年整體營收成長了 39%。"
    item = _item(bad, "雲端營收成長了 39%，整體營收只成長 12%", "部門數字寫成整體")
    reply = lambda p: json.dumps({"patches": [{"原句": "找不到的句子", "改成": "x"}]}) if "只修這幾句" in p else "沒有交稿"
    (final, report, rewrites), _ = _ledger_loop(monkeypatch, [item], reply)
    assert final == DRAFT and report.passed                   # 程式檢查沒違規，不擋稿
    assert report.unresolved and "部門數字寫成整體" in report.unresolved[0]   # 但要讓主編看到


def test_legacy_mode_is_still_available(monkeypatch):
    monkeypatch.setenv("SUBSTACK_REVIEW_MODE", "legacy")
    monkeypatch.setattr(q, "evaluate", lambda a, **k: [])
    monkeypatch.setattr(q, "critique", lambda *a, **k: [])
    final, report, rewrites = q.run_review_loop(DRAFT, fact_block="", auditor="x", eval_kwargs={})
    assert report.passed and final == DRAFT


# ---- 2026-11-16 新流程第一篇實跑：Haiku 沒額度，寫手退到 glm，對帳的也是 glm ----

def test_auditors_never_share_a_family_with_the_writer(monkeypatch):
    monkeypatch.delenv("SUBSTACK_FACT_AUDITORS", raising=False)
    monkeypatch.delenv("SUBSTACK_FACT_AUDIT_FALLBACK", raising=False)
    haiku = "原生 Claude 方案 (Claude CLI / Pro·Max) · 模型 claude-haiku-5-5"
    glm = "gateway · 模型 writer→glm-5.3-flash"
    assert fa.pick_models([haiku]) == ["glm-5.3-flash", "gemini-2.5-flash"]            # 兩個不同家一起查
    assert fa.pick_models([glm]) == ["gemini-2.5-flash", "gemini-3.5-flash"]            # glm 不能審自己，備援補位
    gemini_writer = "antigravity_cli · 模型 Gemini 3.8 Flash (High)"
    assert fa.pick_models([gemini_writer]) == ["glm-5.3-flash"]                         # 寫手是 Gemini 時，Gemini 全部迴避
    assert "glm-5.3-flash" not in fa.pick_models([haiku, "閘道 writer→glm-5.3-flash"])  # 重寫的人也算寫手


def test_gateway_swapping_in_the_writers_model_does_not_count():
    a = _item("節目裡提到 Polymarket 預測今年底前回任的機率六成以上。", "by the end of next year")

    def chat(model, prompt):          # 閘道把逾時的 mistral 換成 glm 來答
        served = "glm-5.3-flash" if model == "mistral-large-4-1t-a52b" else model
        return json.dumps({"items": [a]}, ensure_ascii=False), served

    kept, summary = fa.run(DRAFT, MATERIAL, models=["mistral-large-4-1t-a52b", "gemini-2.5-flash"], chat=chat,
                           deadline_s=5, fallback=[], writers=["gateway · 模型 writer→glm-5.3-flash"])
    assert summary["answered"] == ["gemini-2.5-flash"]
    assert "同一家" in summary["failed"]["mistral-large-4-1t-a52b"] and len(kept) == 1
    # 寫手是 Haiku 時，閘道換上的 glm 是獨立的，可以收，而且標實際作答的模型
    kept, summary = fa.run(DRAFT, MATERIAL, models=["mistral-large-4-1t-a52b"], chat=chat, deadline_s=5,
                           fallback=[], writers=["claude-haiku-5-5"])
    assert summary["answered"] == ["glm-5.3-flash"] and kept[0]["查核模型"] == "glm-5.3-flash"


# ---- 2026-11-16：gemini 三次有一次少最後一個括號，五條發現被當成「沒有問題」 ----

def test_answer_missing_its_last_brace_is_still_read():
    a = _item("節目裡提到 Polymarket 預測今年底前回任的機率六成以上。", "by the end of next year")
    b = _item("這家公司去年整體營收成長了 39%。", "雲端營收成長了 39%，整體營收只成長 12%", "部門數字寫成整體")
    full = json.dumps({"items": [a, b]}, ensure_ascii=False)
    assert len(fa.parse(full)) == 2
    assert len(fa.parse(full[:-1])) == 2                       # 少最外層的 }
    assert len(fa.parse(full[:-2])) == 2                       # 少 ]}
    assert len(fa.parse("```json\n" + full + "\n```")) == 2    # 包在程式碼區塊裡
    assert len(fa.parse(full + "\n以上是我的查核結果。")) == 2   # 後面多了一句話
    cut_mid_item = full[:full.index("部門數字")]                # 第二條寫到「問題」欄一半斷掉
    got = fa.parse(cut_mid_item)                               # 第一條完整；第二條的原句與證據都在，也救回來
    assert [i["原句"] for i in got] == [a["原句"], b["原句"]] and got[1]["素材原文"] == b["素材原文"]
    cut_early = full[:full.index(b["原句"]) + 5]                # 第二條連原句都沒寫完：只留第一條
    assert [i["原句"] for i in fa.parse(cut_early)][0] == a["原句"]
    assert fa.parse('{"items": []}') == [] and not fa.unreadable('{"items": []}')
    assert fa.unreadable('抱歉，我無法完成這個任務 {"items": [壞掉') and not fa.unreadable("")


def test_unreadable_answer_is_a_failure_not_a_clean_bill():
    def chat(model, prompt):
        return "這是我的分析：{items: 沒有照格式" if model == "壞格式" else json.dumps({"items": []})

    _, summary = fa.run(DRAFT, MATERIAL, models=["壞格式", "正常"], chat=chat, deadline_s=5, fallback=[])
    assert summary["answered"] == ["正常"] and "格式壞掉" in summary["failed"]["壞格式"]


def test_patches_missing_a_brace_are_still_applied(monkeypatch):
    bad = "節目裡提到 Polymarket 預測今年底前回任的機率六成以上。"
    fixed = "節目裡提到 Polymarket 預測明年底前回任的機率有 30% 以上。"
    item = _item(bad, "a 30 plus percent chance that he'll return as CEO by the end of next year")
    reply = lambda p: json.dumps({"patches": [{"原句": bad, "改成": fixed}]}, ensure_ascii=False)[:-1]   # 少一個 }
    (final, report, rewrites), _ = _ledger_loop(monkeypatch, [item], reply)
    assert fixed in final and report.patches == 1 and rewrites == 0
