"""2026-10-17 嬌生那篇：搜尋問句太像指令、英文年報被判離題、開場貼標籤、公司面向不足。"""
from substack_radar import aeo_gate
from substack_radar.compose import company_research_queries
from substack_radar.editorial_research import _names_subject, _search_query


def test_instruction_style_queries_become_search_keywords():
    q = _search_query("官方/第一手：調閱 JNJ 2025 年 10-K 中「非經常性項目」、「訴訟負債準備」附註")
    assert not q.startswith("官方") and "調閱" not in q and "「" not in q and "JNJ" in q
    assert len(_search_query("x" * 300)) <= 90


def test_english_annual_report_counts_as_on_topic_for_its_company():
    report = "Johnson & Johnson 2025 Annual Report ... Johnson & Johnson MedTech ... JOHNSON & JOHNSON"
    assert _names_subject("Johnson & Johnson", report)
    assert not _names_subject("Johnson & Johnson", "Pfizer annual report mentions Johnson & Johnson once")
    assert not _names_subject("嬌生", report)          # 中文主題名走原本的比對
    assert not _names_subject("Johnson & Johnson", "8 strategies for boosting manufacturing capacity", min_count=1)


def test_company_research_covers_value_chain_market_production_and_model():
    qs = " ".join(company_research_queries("company", "Johnson & Johnson"))
    for word in ("suppliers", "competitors", "manufacturing", "weaknesses", "10-K"):
        assert word in qs
    assert company_research_queries("podcast", "All-In") == []


def test_lead_with_label_sentence_is_flagged():
    md = ("# 嬌生\n\n我的判斷是：嬌生的獲利擺動來自一次性因素。這篇文章要查的是：獲利品質有沒有變。\n\n"
          "## 小標\n\n內容。")
    assert any(i.rule.startswith("A1b") for i in aeo_gate.check(md))
    clean = "# 嬌生\n\n嬌生 2023 年賺了 352 億美元，隔年只剩 141 億。營收卻一路往上。\n\n## 小標\n\n內容。"
    assert not any(i.rule.startswith("A1b") for i in aeo_gate.check(clean))
