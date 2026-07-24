import asyncio
from types import SimpleNamespace

from src import composer, scorer
from src.schema import CarouselCards, MultiPlatformDraft, PlatformVariant


def test_recovery_scorer_uses_public_interest_contract(monkeypatch):
    monkeypatch.setenv("AUTOMATION_MODE", "recovery")

    prompt = scorer._build_system_instruction()

    assert "台灣公共利益日報" in prompt
    assert "不要因為食安、民生、法律、政策或政府監督題" in prompt
    assert "任務不是尋找科技商業新聞" in prompt
    assert "公司數字" in prompt
    assert "政府宣傳活動、競賽、開幕" in prompt
    assert "日期、人數與主辦機關只能證明活動存在" in prompt
    assert "全市場 benchmark" in prompt
    assert "通常可評 0.70–0.82" in prompt
    assert "程序公告" in prompt and "不得高於 0.65" in prompt


def test_default_scorer_preserves_legacy_technology_filter(monkeypatch):
    monkeypatch.delenv("AUTOMATION_MODE", raising=False)

    prompt = scorer._build_system_instruction()

    assert "科技商業速報" in prompt
    assert "價值鏈搬移" in prompt


def test_recovery_composer_contract_is_platform_scoped_and_actionable():
    prompt = composer._build_recovery_system_instruction(
        "SOURCE AND CORRECTNESS GATE",
        ["fb"],
    )

    assert "這次只撰寫：fb" in prompt
    assert "第一個可見句子的前 45 個中文字" in prompt
    assert "同一讀者現在可採取的一個具體動作" in prompt
    assert "禁止把「已知事實是」「這裡的判讀是」" in prompt
    assert "禁止 Markdown 粗體小標" in prompt
    assert "標題是唯一寫作主題" in prompt
    assert "絕對不可" in prompt
    assert "FB：280–500 字" in prompt
    assert "3–5 個短段落" in prompt
    assert "緊接的下一段可延續同一份來源" in prompt
    assert "Threads：160–240 字" in prompt
    assert "結尾只能有一個問號" in prompt
    assert "SOURCE AND CORRECTNESS GATE" in prompt


def test_recovery_generation_contract_compiles_numbers_and_required_keys():
    prompt = composer._build_recovery_generation_contract(
        "行政院核定高鐵延伸宜蘭，總經費3521億元",
        "行政院說明全案預計11年完工。",
        ["ig"],
    )

    assert "REQUIRED NON-NULL variants: ig" in prompt
    assert "REQUIRED NULL variants: fb, threads" in prompt
    assert "11" in prompt and "3521" in prompt
    assert "render exactly five cards" in prompt
    assert "Do not add today's date" in prompt
    assert "Never round, abbreviate, convert units" in prompt
    assert "STATISTICAL DENSITY BUDGET" in prompt
    assert "3521億元" in prompt
    fb_only = composer._build_recovery_generation_contract(
        "行政院公布政策", "行政院公告政策內容。", ["fb"]
    )
    assert "`carousel` MUST be null" in fb_only


def test_recovery_source_excerpt_hides_nonbudget_market_statistics():
    title = "證交所：加權指數上漲2.30%，市值達142.58兆元"
    content = (
        "根據臺灣證券交易所統計，本週加權指數上漲2.30%。\n"
        "電腦週邊上漲10.29%，綠能下跌9.04%。\n"
        "以上資料為初步統計。"
    )

    excerpt = composer._build_recovery_source_excerpt(title, content)

    assert "根據臺灣證券交易所統計" in excerpt
    assert "2.30%" in excerpt
    assert "10.29%" not in excerpt
    assert "9.04%" not in excerpt


def test_recovery_compose_prompt_removes_legacy_numeric_examples(monkeypatch):
    monkeypatch.setenv("AUTOMATION_MODE", "recovery")
    captured = {}

    async def _fake_call_for_json(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            provider="test",
            raw_error=None,
            data=MultiPlatformDraft(
                ig=PlatformVariant(
                    title="行政院核定3521億高鐵案",
                    body="行政院公告指出總經費3521億元。對通勤族的具體影響是路網將改變；通勤族可以先查詢行政院計畫書。",
                    hashtags=["#台灣政策"],
                    primary_topic_tag="#台灣政策",
                    char_count=70,
                ),
                carousel=CarouselCards(
                    insight_statement="行政院核定宜蘭高鐵案",
                    insight_support="行政院公告列出計畫內容",
                    stat_number="3521億",
                    stat_caption="行政院公告總經費",
                    takeaways=["通勤族先查計畫書"],
                    key_figures=[
                        {"label": "行政院經費", "value": "3521億"},
                        {"label": "行政院工期", "value": "11年"},
                    ],
                ),
            ),
        )

    monkeypatch.setattr(composer, "call_for_json", _fake_call_for_json)
    result = asyncio.run(
        composer.compose_multi_platform(
            "行政院核定高鐵案，總經費3521億元",
            "行政院公告預計11年完工。",
            platforms=["ig"],
        )
    )

    assert result is not None and result.ig is not None
    assert "Allowed material Arabic-number values" in captured["system"]
    assert "REQUIRED NON-NULL variants: ig" in captured["system"]
    assert "$329" not in captured["prompt"]
    assert "2-4 張" not in captured["prompt"]
    assert "五卡契約" in captured["prompt"]

