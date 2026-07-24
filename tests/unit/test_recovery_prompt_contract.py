from src import composer, scorer


def test_recovery_scorer_uses_public_interest_contract(monkeypatch):
    monkeypatch.setenv("AUTOMATION_MODE", "recovery")

    prompt = scorer._build_system_instruction()

    assert "台灣公共利益日報" in prompt
    assert "不要因為食安、民生、法律、政策或政府監督題" in prompt
    assert "任務不是尋找科技商業新聞" in prompt
    assert "公司數字" in prompt


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
    assert "同一讀者現在可採取的一個動作" in prompt
    assert "禁止 Markdown 粗體小標" in prompt
    assert "FB：500–750 字" in prompt
    assert "SOURCE AND CORRECTNESS GATE" in prompt

