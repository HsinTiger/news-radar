"""品質閘門的回歸測試。每一條都對應 2026-08-18 瑞昱稿實際犯過的錯。"""
from substack_radar.fact_reconcile import reconcile
from substack_radar.quality_loop import evaluate

FACTS = {"2025 營收": 1.227e11, "2025 營業利益": 1.439e10, "2022 營業利益": 1.569e10}


def test_catches_order_of_magnitude_error():
    """稿子寫「營業利益僅有 14.39 億」，正確是 143.9 億。"""
    issues = reconcile("2025 年的營業利益卻僅有 14.39 億新臺幣。", FACTS)
    assert len(issues) == 1
    assert issues[0].written == 14.39
    assert abs(issues[0].expected - 1.439e10) < 1


def test_catches_the_same_error_in_the_other_direction():
    """稽核當下我把它更正成「1,439 億」，同樣錯 10 倍。閘門要對稱地抓。"""
    issues = reconcile("營業利益 1,439 億", FACTS)
    assert len(issues) == 1


def test_correct_numbers_produce_no_issue():
    assert reconcile("營收 1,227 億，營業利益 143.9 億。", FACTS) == []


def test_unrelated_amounts_are_not_flagged():
    """對不上事實表的數字一律放過——誤報一次就會讓人把閘門關掉。"""
    assert reconcile("全球市場規模約 7 兆美元。", FACTS) == []


def test_flags_analyst_consensus_written_as_management_guidance():
    """yfinance 給的是分析師共識 EPS，不是公司財測。"""
    art = "管理層的財報指引連續四季落空，管理層誠信度打折。"
    kinds = [v.kind for v in evaluate(art, fact_values={}, has_management_guidance=False)]
    assert "證據張冠李戴" in kinds


def test_allows_guidance_wording_when_real_guidance_exists():
    art = "管理層指引下修。"
    assert evaluate(art, fact_values={}, has_management_guidance=True) == []


def test_correct_consensus_wording_passes():
    art = "實際 EPS 連續四季低於分析師共識，賣方預估與實際脫節。"
    assert evaluate(art, fact_values={}, has_management_guidance=False) == []


def test_auditor_never_shares_a_family_with_the_writer():
    """寫手鏈的鏈尾也是 Claude Opus 4.6。稽核若用同一個模型，
    「不同模型互稽」的保證就沒了——同一個模型讀自己的輸出會重現同一組盲點。"""
    from substack_radar.quality_loop import auditor_for, AUDIT_MODEL, AUDIT_FALLBACK_MODEL

    assert auditor_for("Gemini 3.7 Flash (High)") == AUDIT_MODEL
    assert auditor_for("Claude Opus 4.6 (Thinking)") == AUDIT_FALLBACK_MODEL
    # generated_by 是「antigravity_cli · 模型 X」這種字串，也要判得出家族
    assert auditor_for("antigravity_cli · 模型 Claude Opus 4.6 (Thinking)") == AUDIT_FALLBACK_MODEL
