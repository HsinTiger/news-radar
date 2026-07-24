from __future__ import annotations

from run_pipeline import (
    _deterministic_recovery_closing_repair,
    _deterministic_recovery_hashtag_prune,
    _deterministic_recovery_inference_prune,
    _deterministic_recovery_stat_prune,
    _deterministic_recovery_utility_repair,
    _is_recovery_market_benchmark,
    _quality_rewrite_penalty,
    _recovery_rewrite_guidance,
)
from src.content_quality_guard import QualityIssue, check_platform_style, check_quality


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
        source_label="證交所 官方訊息",
    )

    assert repaired.endswith("對照證交所本週 2.3% 的漲幅，你的持股有跑贏嗎？")
    assert repaired.count("？") == 1
    assert "fact_without_local_source" not in {
        issue.code for issue in check_quality(repaired, recovery=True)
    }


def test_deterministic_market_closing_does_not_attribute_unknown_source() -> None:
    body = "市場本週上漲。\n\n你的持股有跑贏 2.3% 嗎？"

    repaired = _deterministic_recovery_closing_repair(
        body,
        platform="threads",
        topic="tw_stocks",
        source_evidence_text="加權指數上漲2.30%。",
        source_label="user_submission",
    )

    assert "2.3%" not in repaired
    assert "user_submission" not in repaired


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

    assert "若你的報酬跑輸大盤，先比較產業配置與個股選擇" in repaired
    assert "missing_reader_utility" not in codes


def test_deterministic_market_utility_precedes_question_and_hashtags() -> None:
    body = (
        "根據證交所統計，加權指數上漲2.3%。\n\n"
        "對照證交所本週 2.3% 的漲幅，你的投資組合有跑贏嗎？\n\n"
        "#台股 #投資 #市場分析"
    )

    repaired = _deterministic_recovery_utility_repair(
        body,
        topic="tw_stocks",
    )

    assert repaired.index("若你的報酬跑輸大盤") < repaired.index("你的投資組合有跑贏嗎？")
    assert repaired.index("你的投資組合有跑贏嗎？") < repaired.index("#台股")


def test_deterministic_market_stat_prune_drops_secondary_stat_paragraph() -> None:
    body = (
        "根據證交所 7 月 24 日公告，加權指數本週上漲2.3%，"
        "上市股票總市值達142.58兆元。\n\n"
        "電腦週邊上漲10.29%，綠能下跌9.04%，半導體成交占比37.64%。\n\n"
        "若你的報酬跑輸大盤，先比較產業配置與個股選擇。\n\n"
        "下週哪些產業會延續漲勢？"
    )
    source = "加權指數上漲2.30%，上市股票總市值達142.58兆元。"

    pruned = _deterministic_recovery_stat_prune(
        body,
        topic="tw_stocks",
        source_evidence_text=source,
    )
    repaired = _deterministic_recovery_closing_repair(
        pruned,
        platform="threads",
        topic="tw_stocks",
        source_evidence_text=source,
        source_label="證交所 官方訊息",
    )
    codes = {
        issue.code
        for issue in check_platform_style(
            "threads",
            repaired,
            title="台股週報",
            recovery=True,
        )
    }

    assert "10.29%" not in repaired
    assert "9.04%" not in repaired
    assert "37.64%" not in repaired
    assert "platform_stat_overload" not in codes
    assert "generic_engagement_bait" not in codes


def test_deterministic_facebook_repairs_question_before_hashtags() -> None:
    body = (
        "根據證交所公告，加權指數本週上漲2.3%。\n\n"
        "投資人可比較自己的同期間報酬與產業曝險。\n\n"
        "下週臺股能否延續漲勢？哪些產業將成為資金焦點？\n\n"
        "#台股 #證交所 #市場數據 #投資"
    )
    source = "加權指數本週上漲2.3%。"

    pruned = _deterministic_recovery_hashtag_prune(body, platform="fb")
    repaired = _deterministic_recovery_closing_repair(
        pruned,
        platform="fb",
        topic="tw_stocks",
        source_evidence_text=source,
        source_label="證交所 官方訊息",
    )
    codes = {
        issue.code
        for issue in check_platform_style(
            "facebook",
            repaired,
            title="台股週報",
            recovery=True,
        )
    }

    assert repaired.count("#") == 3
    assert repaired.count("？") == 1
    assert repaired.index("你的投資組合有跑贏嗎？") < repaired.index("#台股")
    assert "platform_hashtag_overload" not in codes
    assert "multiple_closing_questions" not in codes
    assert "generic_engagement_bait" not in codes


def test_deterministic_market_inference_prune_keeps_sourced_fact() -> None:
    body = (
        "根據證交所統計，加權指數上漲2.3%，總市值達142.58兆元。"
        "這一漲幅意味著市場情緒有所回暖，可能吸引更多資金進入市場。\n\n"
        "投資人可比較自己的同期間報酬。"
    )
    source = "證交所統計顯示，加權指數上漲2.3%，總市值達142.58兆元。"

    repaired = _deterministic_recovery_inference_prune(
        body,
        topic="tw_stocks",
        source_evidence_text=source,
    )

    assert "加權指數上漲2.3%" in repaired
    assert "市場情緒有所回暖" not in repaired
    assert "吸引更多資金" not in repaired


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


def test_recovery_market_benchmark_excludes_company_procedure_notice() -> None:
    assert _is_recovery_market_benchmark(
        topic="tw_stocks",
        source_label="證交所 官方訊息",
        title="本週發行量加權股價指數上漲2.3%",
        content="上市股票總市值達142.58兆元。",
    )
    assert not _is_recovery_market_benchmark(
        topic="tw_stocks",
        source_label="證交所 官方訊息",
        title="英柏得科技申請股票上市",
        content="公司送件申請上市，公告資本額與產品。",
    )
