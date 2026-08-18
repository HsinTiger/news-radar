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


# --- 證據法則 -------------------------------------------------------------
_SRCS = [{"publisher": "moneyweekly.com.tw", "title": "瑞昱獲利攀高峰",
          "excerpt": "瑞昱在乙太網路晶片的全球市佔率超過 50%，音訊晶片在 PC 市場高達 70% 以上。"}]


def test_evidence_rejects_a_source_not_in_the_list():
    """瑞昱稿把「戴爾電腦」寫成市調機構，而戴爾根本不在取材清單裡。"""
    from substack_radar.evidence_gate import check
    issues = check("根據 戴爾電腦 等機構數據，市佔率超過 50%。", sources=_SRCS)
    assert any(i.rule.startswith("E1") for i in issues)


def test_evidence_rejects_a_number_that_is_nowhere_in_the_sources():
    from substack_radar.evidence_gate import check
    issues = check("根據 moneyweekly 數據，交換器市佔率超過 88%。", sources=_SRCS)
    assert any(i.rule.startswith("E3") for i in issues)


def test_evidence_accepts_a_number_present_in_a_source_excerpt():
    from substack_radar.evidence_gate import check
    assert check("根據 moneyweekly 數據，乙太網路晶片市佔率超過 50%。", sources=_SRCS) == []


def test_evidence_ignores_numbers_without_attribution():
    """沒有歸屬語氣的數字多半是從事實表推導的比率，不在管轄範圍。"""
    from substack_radar.evidence_gate import check
    assert check("營益率只剩 10% 左右，這是高毛利低營益率的核心。", sources=_SRCS) == []


def test_locator_points_at_source_index_and_offset():
    """owner 要的是「哪篇文章的哪一段第幾個字」。"""
    from substack_radar.evidence_gate import locate
    where = locate("50", "%", _SRCS, "")
    assert where and "來源 #1" in where and "第" in where and "字" in where


def test_locator_requires_the_unit_not_just_the_digits():
    """只比裸數字時，「50%」曾命中來源裡的「1,350 億美元」、
    「15%」命中「合理本益比可給到 15 倍」——定位成功但完全不是同一件事。"""
    from substack_radar.evidence_gate import locate
    srcs = [{"publisher": "x", "excerpt": "全球半導體設備銷售額將由 1,350 億美元成長。"}]
    assert locate("50", "%", srcs, "") is None


def test_locator_folds_fullwidth_percent():
    """來源常寫全形「超過70％」，稿子寫半形「70%」。"""
    from substack_radar.evidence_gate import locate
    srcs = [{"publisher": "moneyweekly", "excerpt": "瑞昱乙太網路晶片全球市占率超過70％。"}]
    assert locate("70", "%", srcs, "", topic="瑞昱乙太網路晶片市佔率")


def test_locator_prefers_the_topically_closest_hit():
    """同一個「10%」可能在多個來源出現，要挑跟原句最相關的那個。"""
    from substack_radar.evidence_gate import locate
    srcs = [
        {"publisher": "old", "excerpt": "BGE IC 將於 2006 年占出貨比重超過 10%。"},
        {"publisher": "new", "excerpt": "Wi-Fi 7 在 PC 與路由器的採用率分別約為 15% 與 10%。"},
    ]
    where = locate("10", "%", srcs, "", topic="Wi-Fi 7 在 PC 與路由器的滲透率")
    assert where and "來源 #2" in where


def test_named_source_matching_is_not_greedy():
    """「理財周刊引述 Dell 的統計」要認得出來源標題裡的「理財周刊」。"""
    from substack_radar.evidence_gate import check
    srcs = [{"publisher": "moneyweekly.com.tw", "title": "數位家庭起飛 瑞昱獲利攀高峰 - 理財周刊",
             "excerpt": "瑞昱乙太網路晶片全球市占率超過70％。"}]
    issues = check("根據理財周刊引述 Dell 的統計，市佔率超過 70%。", sources=srcs)
    assert issues == []


def test_foreign_currency_amounts_are_not_reconciled():
    """2026-08-18 聯發科稿：「營收規模將衝上 70 至 120 億美元、拿下全球 ASIC
    市場」被比對到聯發科台幣淨利 1,181 億（×10 差 1.6%，落在容差內）。
    事實表是公司本位幣，稿子裡的外幣金額多半在講別人的市場規模。"""
    facts = {"2022 淨利": 1.181e11, "2024 營業利益": 1.024e11}
    assert reconcile("營收規模將衝上 70 至 120 億美元。", facts) == []
    assert reconcile("上看 20 億美元，比原先預期的 10 億美元翻倍。", facts) == []


def test_only_ten_fold_slips_are_reported():
    """真正的量級災難是「十億 vs 億」。查到 100、1000 倍只會撞出巧合。"""
    facts = {"營業利益": 1.024e11}
    assert reconcile("這個市場約 10 億元。", facts) == []      # ×100，不查
    assert len(reconcile("營業利益 102.4 億元。", facts)) == 1  # ×10，要抓


def test_chinese_numerals_with_units_are_flagged():
    """2026-08-18 三篇實測，同一條管線的中文數字分佈是 0／7／81 處——
    不是規則問題，是模型每次心情不同，所以要有閘門而不是只靠 prompt。"""
    from substack_radar.quality_loop import chinese_numerals
    hits = chinese_numerals("股價站上三千八百八十五元、本益比六十四倍，營收五千九百六十億。")
    assert len(hits) == 3


def test_ordinary_chinese_numerals_are_left_alone():
    """序數與慣用語不是量，不該被抓。"""
    from substack_radar.quality_loop import chinese_numerals
    assert chinese_numerals("第一次看到這三大廠商，兩者都值得注意。") == []


def test_cagr_label_states_the_real_span():
    """4 個年度資料點（2022→2025）只跨 3 年。標籤原本寫「近 4 年 CAGR」，
    寫手照抄成「過去四年的複合年成長率」——算式對、標籤錯。"""
    from substack_radar.company_financials import _format_md
    md = _format_md({"years": [2025, 2024, 2023, 2022], "currency": "TWD",
                     "revenue_b": [595.97, 530.59, 433.45, 548.8], "rev_cagr": 2.8})
    assert "2022→2025（3 年）" in md
    assert "近 4 年" not in md


def test_stale_target_prices_are_flagged():
    """2026-08-18 聯發科稿引用 2025-11 的報導（9 個月前）裡的「大摩目標價
    1,288 元、高盛 1,400 元」，而當時股價 3,885——等於暗示外資看空 65%。
    來源活著、也切題，就是過期。"""
    from substack_radar.evidence_gate import stale_claims
    srcs = [{"publisher": "n.yam.com", "url": "https://n.yam.com/Article/20251124477055",
             "excerpt": "摩根士丹利調降聯發科目標價至1288元，高盛下修至1400元。"}]
    issues = stale_claims("摩根士丹利調降目標價至 1,288 元，高盛下修至 1,400 元。", srcs)
    assert len(issues) == 2 and all(i.rule.startswith("E4") for i in issues)


def test_fresh_sources_do_not_trigger_staleness():
    from substack_radar.evidence_gate import stale_claims
    srcs = [{"publisher": "x", "url": "https://x.com/2026/08/13/a", "excerpt": "目標價1288元"}]
    assert stale_claims("目標價 1,288 元。", srcs) == []


def test_source_date_is_read_from_the_url():
    from datetime import date
    from substack_radar.source_dates import published_on, age_days
    assert published_on("https://n.yam.com/Article/20251124477055") == date(2025, 11, 24)
    assert age_days("https://n.yam.com/Article/20251124477055", today=date(2026, 8, 18)) == 267
    assert published_on("https://money.udn.com/money/story/5612/9675870") is None


def test_undated_source_age_is_estimated_from_content_years():
    """瑞昱稿引用的理財周刊文章沒有任何日期格式，卻是約 18 年前的報導，
    提供了被寫成現況的市佔率。「日期不明」不等於新鮮。"""
    from datetime import date
    from substack_radar.source_dates import newest_year_mentioned, estimated_age_days
    text = "2003年起…2005全年營收…2006年占出貨比重…2007年展望"
    assert newest_year_mentioned(text) == 2007
    age = estimated_age_days("https://example.com/no-date", text, date(2026, 8, 18))
    assert age and age > 6000


def test_recent_content_is_not_aged_by_the_estimator():
    from datetime import date
    from substack_radar.source_dates import estimated_age_days
    assert estimated_age_days("https://x.com/a", "2026年第二季營收", date(2026, 8, 18)) == 0
