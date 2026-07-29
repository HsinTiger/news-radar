from __future__ import annotations

from run_pipeline import (
    _deterministic_food_safety_carousel,
    _deterministic_food_safety_variant,
    _deterministic_recovery_closing_repair,
    _deterministic_recovery_hashtag_fields,
    _deterministic_recovery_hashtag_prune,
    _deterministic_recovery_inference_prune,
    _deterministic_reserve_framing_repair,
    _deterministic_recovery_stat_prune,
    _deterministic_recovery_title_prune,
    _deterministic_market_benchmark_carousel,
    _deterministic_market_benchmark_variant,
    _deterministic_recovery_utility_repair,
    _is_recovery_market_benchmark,
    _is_recovery_food_safety_investigation,
    _quality_rewrite_penalty,
    _recovery_rewrite_guidance,
)
from src.content_quality_guard import QualityIssue, check_platform_style, check_quality
from src.composer import finalize_variant
from src.schema import PlatformVariant
from substack_radar.cards import build_cards


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


def test_recovery_rewrite_contract_repairs_reserve_recency() -> None:
    guidance = _recovery_rewrite_guidance(
        {"reserve_source_missing_date", "reserve_source_false_recency"},
        source_evidence_text=(
            "行政院公告油價調整\n內容\n2026-07-25T14:56:00+00:00"
        ),
        source_label="行政院 消費警訊",
    )

    assert any("OFFICIAL RESERVE FRAMING" in line for line in guidance)


def test_deterministic_reserve_framing_adds_date_and_removes_false_recency() -> None:
    repaired = _deterministic_reserve_framing_repair(
        (
            "根據臺灣證券交易所本週統計，加權指數上漲2.3%。\n\n"
            "目前市場已全面回暖。請追蹤最新公告。"
        ),
        source_label="證交所 官方訊息",
        source_published_at="2026-07-24T10:00:00+00:00",
    )

    assert repaired.startswith("根據證交所7月24日公告，")
    assert "目前市場" not in repaired
    assert "最新公告" not in repaired
    assert "後續公告" in repaired


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


def test_deterministic_policy_closing_is_reader_answerable() -> None:
    body = (
        "立法院三讀通過兒少未來帳戶條例。\n\n"
        "家長可追蹤衛福部後續公布的實施細則與申請方式。\n\n"
        "衛福部預計何時公布首次撥款日期？\n\n"
        "#公共政策"
    )

    repaired = _deterministic_recovery_closing_repair(
        body,
        platform="threads",
        topic="tw_politics",
        source_evidence_text="立法院三讀通過兒少未來帳戶條例。",
        source_label="公視新聞 PTS",
    )
    codes = {
        issue.code
        for issue in check_platform_style(
            "threads",
            repaired,
            title="兒少未來帳戶條例三讀",
            recovery=True,
        )
    }

    assert "如果你家有未成年孩子" in repaired
    assert repaired.index("你最想先確認") < repaired.index("#公共政策")
    assert "generic_engagement_bait" not in codes
    assert repaired.count("？") == 1


def test_deterministic_current_affairs_closing_keeps_facts_unchanged() -> None:
    factual = "臺中市環保局表示將依空汙法裁處。"
    body = factual + "\n\n福壽公司能否重建消費者信任？"

    repaired = _deterministic_recovery_closing_repair(
        body,
        platform="threads",
        topic="current_affairs",
        source_evidence_text=factual,
        source_label="公視新聞 PTS",
    )

    assert repaired.startswith(factual)
    assert "如果事件影響到你所在的社區" in repaired
    assert "福壽公司能否" not in repaired


def test_deterministic_energy_closing_uses_source_context_over_topic_label() -> None:
    body = (
        "經濟部能源署表示，臺灣天然氣儲備至少11天。\n\n"
        "面對封鎖風險，臺灣如何確保供應不中斷？\n\n"
        "#國防安全"
    )

    repaired = _deterministic_recovery_closing_repair(
        body,
        platform="threads",
        topic="tw_politics",
        source_evidence_text="臺灣維持天然氣儲備，並規劃東部海岸應變演習。",
        source_label="中央社",
    )

    assert "如果能源供應中斷影響到你" in repaired
    assert "適用資格" not in repaired
    assert repaired.index("官方演習結果？") < repaired.index("#國防安全")


def test_v34_deterministic_food_safety_closing_uses_source_context() -> None:
    body = (
        "食藥署公布中聯油脂案調查結果。\n\n"
        "民眾可追蹤立法院後續審議。\n\n"
        "如果政策適用你或家人，你最想先確認資格、開始日期，還是申請方式？\n\n"
        "#食品安全"
    )

    repaired = _deterministic_recovery_closing_repair(
        body,
        platform="fb",
        topic="tw_politics",
        source_evidence_text="食藥署公布中聯油脂食品安全調查結果。",
        source_label="食藥署 本署新聞",
    )

    assert "原料風險、抽驗結果，還是違規改善進度" in repaired
    assert "資格" not in repaired
    assert repaired.index("違規改善進度？") < repaired.index("#食品安全")


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


def test_v35_deterministic_food_safety_utility_is_concrete() -> None:
    body = (
        "食藥署公布中聯油脂案調查結果。\n\n"
        "行政院通過食品安全衛生管理法修正草案。\n\n"
        "你買食用油時，最希望主管機關優先公開哪一項資訊？\n\n"
        "#食品安全"
    )

    repaired = _deterministic_recovery_utility_repair(
        body,
        topic="tw_politics",
        source_evidence_text="食藥署公布食品安全調查；修法聚焦源頭、製程、異常通報與品質管理。",
    )
    codes = {
        issue.code
        for issue in check_quality(
            repaired,
            title="中聯油脂食安調查",
            recovery=True,
            source_text=repaired,
        )
    }

    assert "消費者可追蹤立法院審議與食藥署後續公告" in repaired
    assert "核對源頭、製程、異常通報與品質管理規則是否落地" in repaired
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


def test_deterministic_repair_prunes_structured_hashtag_fields() -> None:
    variant = PlatformVariant(
        title="立法院審預算",
        body="根據立法院議事資料，本週將審議新年度預算。",
        primary_topic_tag="#公共監督",
        hashtags=["#公共監督", "#立法院", "#預算", "#政治", "#台灣"],
        char_count=0,
    )

    repaired = _deterministic_recovery_hashtag_fields(variant, platform="fb")
    finalized, full_text, ok = finalize_variant(repaired, "fb")

    assert ok is True
    assert finalized.primary_topic_tag == "#公共監督"
    assert finalized.hashtags == ["#公共監督", "#立法院", "#預算"]
    assert full_text.count("#") == 3
    assert "platform_hashtag_overload" not in {
        issue.code
        for issue in check_platform_style(
            "facebook",
            full_text,
            title=finalized.title,
            recovery=True,
        )
    }


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


def test_v34_market_inference_prune_removes_flow_causality() -> None:
    source = "證交所公布，上週外資在集中市場賣超470.64億元。"
    body = (
        source
        + "這反映外資對不同產業的看法，可能帶動個股短期波動。\n\n"
        "外資大幅賣超可能影響持股價值與市場信心。"
    )

    repaired = _deterministic_recovery_inference_prune(
        body,
        topic="tw_stocks",
        source_evidence_text=source,
    )

    assert "賣超470.64億元" in repaired
    assert "反映外資" not in repaired
    assert "短期波動" not in repaired
    assert "市場信心" not in repaired


def test_v35_market_inference_prune_removes_price_and_sentiment_causality() -> None:
    source = "證交所公布，上週外資在集中市場賣超470.64億元。"
    body = (
        source
        + "外資集中買賣超容易引發相關個股價格波動，投資人可能感受到市場情緒的變化。\n\n"
        "外資持股比例變動會牽動整體市場信心。"
    )

    repaired = _deterministic_recovery_inference_prune(
        body,
        topic="tw_stocks",
        source_evidence_text=source,
    )

    assert "賣超470.64億元" in repaired
    assert "價格波動" not in repaired
    assert "市場情緒" not in repaired
    assert "市場信心" not in repaired


def test_deterministic_market_prunes_vague_fb_interpretation_and_title() -> None:
    title = "本週臺股指數上漲2.30%，投資者需注意市場變化。"
    body = (
        "根據證交所統計，加權指數上漲2.3%。"
        "這意味著市場情況正在改善，可能影響到投資決策。\n\n"
        "數據顯示出市場的活躍度，能幫助投資人把握市場動向，"
        "做出明智的投資選擇。\n\n"
        "投資人可比較自己的同期間報酬。"
    )
    source = "根據證交所統計，加權指數上漲2.3%。"

    repaired_title = _deterministic_recovery_title_prune(title)
    repaired_body = _deterministic_recovery_inference_prune(
        body,
        topic="tw_stocks",
        source_evidence_text=source,
    )

    assert repaired_title == "本週臺股指數上漲2.30%"
    assert "市場情況正在改善" not in repaired_body
    assert "市場的活躍度" not in repaired_body
    assert "明智的投資選擇" not in repaired_body
    assert "投資人可比較自己的同期間報酬" in repaired_body


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


def test_recovery_food_safety_template_is_fail_closed() -> None:
    source = (
        "中聯油脂事件第三方獨立調查指出苯(a)駢芘超標並非單一因素。"
        "行政院通過食品安全衛生管理法修正草案。"
    )

    assert _is_recovery_food_safety_investigation(
        source_label="食藥署 本署新聞",
        title="食藥署公布中聯油脂事件第三方調查",
        content=source,
    )
    assert not _is_recovery_food_safety_investigation(
        source_label="食藥署 本署新聞",
        title="一般食品抽驗結果",
        content="食藥署公布例行抽驗。",
    )
    assert not _is_recovery_food_safety_investigation(
        source_label="一般媒體",
        title="中聯油脂事件第三方調查",
        content=source,
    )


def test_deterministic_food_safety_copy_is_source_bounded() -> None:
    source = (
        "食藥署27日公布中聯油脂案第三方獨立調查結果，苯(a)駢芘超標並非單一因素。"
        "調查指出原料管理、製程管控與檢驗監測等多項缺失。"
        "行政院7月23日通過食品安全衛生管理法修正草案，聚焦源頭、製程、"
        "異常通報、品質管理與數位治理。"
    )
    seed = PlatformVariant(
        title="LLM title",
        body="LLM body",
        hashtags=[],
        char_count=0,
    )

    for platform, canonical in (
        ("threads", "threads"),
        ("fb", "facebook"),
        ("ig", "instagram"),
    ):
        rewritten = _deterministic_food_safety_variant(seed, platform=platform)
        _variant, full_text, _ok = finalize_variant(rewritten, platform)
        issues = check_quality(
            full_text,
            title="中聯油脂食安調查",
            recovery=True,
            source_text=source,
        ) + check_platform_style(
            canonical,
            full_text,
            title="中聯油脂食安調查",
            recovery=True,
        )

        assert "有望降低" not in full_text
        assert "今年完成審議" not in full_text
        assert "所有臺灣消費者" not in full_text
        assert not [issue for issue in issues if issue.severity == "rewrite"]

    carousel = _deterministic_food_safety_carousel()
    assert len(build_cards(title="中聯油脂食安調查", subtitle="", carousel=carousel)) == 3


def test_deterministic_market_benchmark_copy_is_source_bounded() -> None:
    source = (
        "本週發行量加權股價指數漲幅約為2.30%，"
        "上市股票總市值達142.58兆元。"
    )
    seed = PlatformVariant(
        title="LLM title",
        body="LLM body",
        hashtags=[],
        char_count=0,
    )
    rewritten = _deterministic_market_benchmark_variant(
        seed,
        platform="fb",
        source_evidence_text=source,
    )
    _variant, full_text, _ok = finalize_variant(rewritten, "fb")
    issues = check_quality(
        full_text,
        title="台股週報",
        recovery=True,
        source_text=source,
    ) + check_platform_style(
        "facebook",
        full_text,
        title="台股週報",
        recovery=True,
    )

    assert "市場正在改善" not in full_text
    assert "所有持有" not in full_text
    assert "不等於每個投資組合" in full_text
    assert not [issue for issue in issues if issue.severity == "rewrite"]

    carousel = _deterministic_market_benchmark_carousel(source)
    assert carousel is not None
    assert len(build_cards(title="台股週報", subtitle="", carousel=carousel)) == 3
