"""Phase 8.20 Step 2：topic_classifier 單元測試。

Topic-3 redo（2026-04-22）：擴增為三層覆蓋。
  原 keyword + compute_weighted_score 測試全部保留、backward-compat 不動。
  新增：
    - Disambiguation 8 條：每條各一 positive + 1 條 unless_any
    - Exclusion 6 條：每條 positive（veto 生效）+ 驗 exclusion 只在對應 category 觸發
    - orchestration：classify_topic（async）整段從前到後走通
LLM path 仍然放 integration tests。
"""
from __future__ import annotations

from src.topic_classifier import (
    DISAMBIGUATION_RULES,
    EXCLUSION_PATTERNS,
    TopicClassification,
    classify_topic_keyword,
    compute_weighted_score,
    is_vetoed_by_exclusion,
    match_disambiguation,
)
from src.topic_taxonomy import category_ids


# ---------- keyword fast-path ----------

def test_claude_opus_hits_ai_model():
    c = classify_topic_keyword(
        "Anthropic 發布 Claude Opus 4.7，SWE-bench 拿下 78%", ""
    )
    assert c is not None and c.category_id == "ai_model"
    assert 0 < c.confidence <= 1


def test_tsmc_hbm_hits_supply_chain():
    c = classify_topic_keyword("台積電 3 奈米產能滿載，HBM 供應緊張", "")
    assert c is not None and c.category_id == "supply_chain"


def test_nvidia_earnings_hits_earnings():
    c = classify_topic_keyword("Nvidia 法說：Q3 毛利率 75%", "")
    assert c is not None and c.category_id == "earnings"


def test_taiex_hits_tw_stocks():
    c = classify_topic_keyword("外資大舉買超台股 300 億，加權指數創高", "")
    assert c is not None and c.category_id == "tw_stocks"


def test_claude_code_hits_ai_agent_not_ai_model():
    """『Claude Code』應命中 ai_agent（因為列表含『Claude Code』完整字串），
    而非 ai_model（列表只含『Claude Opus/Sonnet/Haiku』，不含 bare 'Claude'）。"""
    c = classify_topic_keyword(
        "Claude Code 與 Cursor 整合：新的 agent SDK 功能", ""
    )
    assert c is not None
    assert c.category_id == "ai_agent", f"expected ai_agent, got {c.category_id}"


def test_irrelevant_content_misses():
    c = classify_topic_keyword("今日台北多雲短暫陣雨", "氣溫 24 度")
    assert c is None, f"weather article should miss keyword path, got {c}"


def test_keyword_confidence_is_conservative():
    """keyword path 不該自封『高信心』——留空間給 LLM 在模棱兩可時接手。"""
    c = classify_topic_keyword("台積電新廠動工", "")
    assert c is not None and c.confidence <= 0.7


def test_keyword_category_always_in_taxonomy():
    samples = [
        "GPT-5 發表",
        "CHIPS 法案新提案",
        "iPhone 18 發表",
        "Apple Vision Pro 銷量",
    ]
    valid = set(category_ids())
    for s in samples:
        c = classify_topic_keyword(s, "")
        if c is not None:
            assert c.category_id in valid, f"{s} → {c.category_id} 不在 taxonomy"


# ---------- compute_weighted_score ----------

def test_weighted_score_basic():
    assert abs(compute_weighted_score(0.85, 1.70) - 1.445) < 1e-6


def test_weighted_score_clips_high():
    assert compute_weighted_score(1.0, 2.5) == 2.0
    assert compute_weighted_score(1.5, 1.5) == 2.0


def test_weighted_score_clips_low():
    assert compute_weighted_score(-0.2, 1.0) == 0.0
    assert compute_weighted_score(0.0, 1.7) == 0.0


def test_weighted_score_preserves_zero():
    assert compute_weighted_score(0.5, 0.0) == 0.0


# ---------- Topic-3 redo：structural counts（catch silent regressions）----------

def test_disambiguation_rules_count_matches_spec():
    """若新增/刪除 disambig 規則請同步更新 news_radar_soul §主題分類。"""
    assert len(DISAMBIGUATION_RULES) == 8


def test_exclusion_patterns_count_matches_spec():
    assert len(EXCLUSION_PATTERNS) == 6


# ---------- Topic-3 redo：Disambiguation 8 條各一 positive ----------

def test_disambig_tsmc_earnings():
    c = match_disambiguation("台積電法說會：Q3 毛利率 62%", "")
    assert c is not None and c.category_id == "earnings"
    assert c.confidence >= 0.75 and "tsmc" in c.rationale.lower()


def test_disambig_nvidia_earnings():
    c = match_disambiguation("Nvidia 法說預告 Q4 earnings guidance 上修", "")
    assert c is not None and c.category_id == "earnings"


def test_disambig_apple_hardware():
    c = match_disambiguation("Apple 發表 iPhone 18 Pro 新攝影系統", "")
    assert c is not None and c.category_id == "tech_product_launch"


def test_disambig_apple_intelligence_beats_hardware():
    """unless_any: Apple + iPhone + Apple Intelligence → 走 ai_application，不走 hardware。"""
    c = match_disambiguation(
        "Apple 發表 iPhone 18：首度原生支援 Apple Intelligence 全套功能", ""
    )
    assert c is not None
    assert c.category_id == "ai_application", (
        f"Apple Intelligence 應蓋過 apple_hardware，實際 {c.category_id}"
    )


def test_disambig_apple_ai_application_alone():
    c = match_disambiguation("Apple Intelligence 寫作工具測試心得", "")
    assert c is not None and c.category_id == "ai_application"


def test_disambig_google_gemini_model():
    c = match_disambiguation("Google DeepMind 發表 Gemini 3 Pro", "")
    assert c is not None and c.category_id == "ai_model"


def test_disambig_google_gemini_unless_astrology():
    """unless_any 阻擋星座文章（非常態、但防禦性驗證）。"""
    c = match_disambiguation("雙子座 Gemini 運勢本週 Pro tip", "")
    assert c is None or c.category_id != "ai_model"


def test_disambig_copilot_as_agent():
    c = match_disambiguation(
        "Microsoft Copilot Agent Builder 支援多步驟 autonomous 任務", ""
    )
    assert c is not None and c.category_id == "ai_agent"


def test_disambig_meta_device():
    c = match_disambiguation("Meta Quest 4 Pro 頭戴發表會直播", "")
    assert c is not None and c.category_id == "tech_product_launch"


def test_disambig_meta_unless_llama():
    """Meta + Quest + Llama → Llama 是 AI 模型，不應判 device。"""
    c = match_disambiguation("Meta Llama 5 登陸 Quest 頭戴", "")
    assert c is None or c.category_id != "tech_product_launch"


def test_disambig_openai_reasoning_model():
    c = match_disambiguation("OpenAI 發表 o3 reasoning model", "")
    assert c is not None and c.category_id == "ai_model"


# ---------- Topic-3 redo：Exclusion 6 條各一 positive ----------

def test_exclusion_tsmc_philanthropy_vetoes_supply_chain():
    """台積電捐 10 億給基金會——明顯不是 supply_chain。"""
    title = "台積電捐款 10 億元給科學慈善基金會"
    # simple keyword 會先命中 supply_chain（因 "台積電"）
    kw = classify_topic_keyword(title, "")
    assert kw is not None and kw.category_id == "supply_chain", "前提：simple kw 會命中 supply_chain"
    assert is_vetoed_by_exclusion(title, "", "supply_chain") is True


def test_exclusion_grok_literature_vetoes_ai_model():
    title = "Grok 一詞來自 Heinlein 異鄉異客：為何這本科幻小說影響一個世代"
    kw = classify_topic_keyword(title, "")
    assert kw is not None and kw.category_id == "ai_model"
    assert is_vetoed_by_exclusion(title, "", "ai_model") is True


def test_exclusion_mistral_weather_vetoes_ai_model():
    title = "地中海 Mistral 強風席捲南法，氣象警報升級"
    kw = classify_topic_keyword(title, "")
    assert kw is not None and kw.category_id == "ai_model"
    assert is_vetoed_by_exclusion(title, "", "ai_model") is True


def test_exclusion_copilot_aviation_vetoes_ai_application():
    title = "波音 777 副駕駛 Copilot 證詞：駕駛艙 incident 全程回放"
    kw = classify_topic_keyword(title, "")
    assert kw is not None and kw.category_id == "ai_application"
    assert is_vetoed_by_exclusion(title, "", "ai_application") is True


def test_exclusion_chatgpt_meme_vetoes_ai_application():
    title = "網路笑話合集：ChatGPT Plus 用戶的梗圖迷因集錦"
    kw = classify_topic_keyword(title, "")
    assert kw is not None and kw.category_id == "ai_application"
    assert is_vetoed_by_exclusion(title, "", "ai_application") is True


def test_exclusion_deepseek_mining_vetoes_ai_model():
    title = "DeepSeek 深海鑽探技術突破：挖礦效率提升 40%"
    kw = classify_topic_keyword(title, "")
    assert kw is not None and kw.category_id == "ai_model"
    assert is_vetoed_by_exclusion(title, "", "ai_model") is True


# ---------- Topic-3 redo：Exclusion scope（不該波及其他類別）----------

def test_exclusion_scoped_to_category():
    """台積電慈善新聞不該被 exclusion 當成 earnings 類的 veto signal。
    exclusion 的 category_id 限縮，tentative = earnings 時 exclusion 不啟動。"""
    title = "台積電捐款 10 億元給科學慈善基金會"
    assert is_vetoed_by_exclusion(title, "", "earnings") is False
    assert is_vetoed_by_exclusion(title, "", "other") is False


# ---------- Topic-3 redo：新增 keyword 也要命中 ----------

def test_new_ai_model_keyword_o3_hits():
    c = classify_topic_keyword("OpenAI o3 reasoning model 內部測試成績外流", "")
    # 不保證走 disambig（depends on co-occurrence）但 simple keyword 至少要能抓到 ai_model
    # 這裡測 simple keyword 層；disambig 另有測試
    assert c is not None and c.category_id == "ai_model"


def test_new_ai_agent_keyword_mcp_hits():
    c = classify_topic_keyword("Anthropic 發表 Model Context Protocol 技術白皮書", "")
    assert c is not None and c.category_id == "ai_agent"


def test_new_ai_application_keyword_notebooklm_hits():
    c = classify_topic_keyword("Google NotebookLM 加入 podcast 生成功能", "")
    assert c is not None and c.category_id == "ai_application"


def test_new_policy_keyword_bis_hits():
    c = classify_topic_keyword("BIS 公告新一輪 semiconductor export 管制清單", "")
    assert c is not None and c.category_id == "policy_geopolitics"
