from __future__ import annotations

from run_pipeline import _recovery_rewrite_guidance


def test_recovery_rewrite_contract_names_source_and_allowed_numbers() -> None:
    guidance = "\n".join(
        _recovery_rewrite_guidance(
            {
                "weak_recovery_hook",
                "unsupported_numeric_claim",
                "uncited_stat",
                "missing_reader_utility",
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
