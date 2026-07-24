from __future__ import annotations

from run_pipeline import (
    _deterministic_recovery_closing_repair,
    _deterministic_recovery_utility_repair,
    _quality_rewrite_penalty,
    _recovery_rewrite_guidance,
)
from src.content_quality_guard import QualityIssue, check_quality


def test_recovery_rewrite_contract_names_source_and_allowed_numbers() -> None:
    guidance = "\n".join(
        _recovery_rewrite_guidance(
            {
                "weak_recovery_hook",
                "unsupported_numeric_claim",
                "uncited_stat",
                "missing_reader_utility",
                "unsupported_audience_extension",
                "platform_stat_overload",
                "multiple_closing_questions",
            },
            source_evidence_text=(
                "食藥署 7 月 24 日公告，19 批油品合格，涉及 501 項產品。"
            ),
            source_label="食藥署 本署新聞",
        )
    )

    assert "first 45 Chinese characters" in guidance
    assert "19" in guidance and "24" in guidance and "501" in guidance
    assert "25" not in guidance
    assert "`食藥署 本署新聞`" in guidance
    assert "In natural prose" in guidance
    assert "Do not use fixed scaffolding" in guidance
    assert "`的具體影響是`" in guidance and "`可以先`" in guidance
    assert "specific Taiwan reader" in guidance
    assert "Do not round, abbreviate" in guidance
    assert "One immediately adjacent paragraph may continue" in guidance
    assert "Delete claims about retirement funds" in guidance
    assert "Remove repeated facts and secondary numbers" in guidance
    assert "Use no more than two market/statistical values" in guidance
    assert "exactly one question mark" in guidance
    assert "FINAL SELF-CHECK BEFORE JSON" in guidance


def test_recovery_rewrite_contract_removes_formula_and_attributes_allegation() -> None:
    guidance = "\n".join(
        _recovery_rewrite_guidance(
            {
                "formulaic_attention_hook",
                "recovery_jargon_pileup",
                "unattributed_sensitive_allegation",
                "missing_taiwan_relevance",
            },
            source_evidence_text="監察院公告調查結果。",
            source_label="user_submission",
        )
    )

    assert "市場以為" in guidance
    assert "Attention must come from the verified consequence" in guidance
    assert "Attribute every allegation" in guidance
    assert "Explicitly name" in guidance
    assert "exact named institution" not in guidance


def test_deterministic_market_closing_repair_uses_source_benchmark() -> None:
    body = "證交所公布本週市場統計。\n\n下週主要產業會延續漲勢嗎？"

    repaired = _deterministic_recovery_closing_repair(
        body,
        platform="threads",
        topic="tw_stocks",
        source_evidence_text="加權指數上漲2.30%，市值達142.58兆元。",
    )

    assert repaired.endswith("你的持股本週有跑贏 2.3% 嗎？")
    assert repaired.count("？") == 1


def test_deterministic_market_utility_repair_is_concrete() -> None:
    body = "證交所公布本週市場統計。\n\n你的持股有跑贏大盤嗎？"

    repaired = _deterministic_recovery_utility_repair(
        body,
        topic="tw_stocks",
    )
    codes = {
        issue.code
        for issue in check_quality(repaired, recovery=True)
    }

    assert "若你的報酬跑輸大盤，先檢查持股產業曝險。" in repaired
    assert "missing_reader_utility" not in codes


def test_best_of_repair_penalty_prefers_fewer_rewrite_issues() -> None:
    one_issue = {
        "threads": [QualityIssue("a", "rewrite", "a")],
    }
    degraded = {
        "threads": [
            QualityIssue("a", "rewrite", "a"),
            QualityIssue("b", "rewrite", "b"),
        ],
    }

    assert _quality_rewrite_penalty(one_issue) < _quality_rewrite_penalty(degraded)
