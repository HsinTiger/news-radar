import sys
import types

import pytest

from substack_radar import composer, editorial_research


def _brief(article_form: str = "argument") -> composer.EditorialResearchBrief:
    return composer.EditorialResearchBrief(
        article_form=article_form,
        source_digest=(
            "這場對談從模型效率出發，最後追到算力需求是否反而上升。"
            "有趣的不是單次推論究竟便宜多少，而是便宜之後會不會出現更多用法、"
            "更長工作流程與更高使用頻率，最終把效率紅利全部吃掉。"
        ),
        compelling_exchange=(
            "主持人追問效率提升為何沒有帶來總算力下降；"
            "來賓主張使用量擴張會吃掉單次成本的下降。"
        ),
        source_claims=["效率提升會擴大可行應用", "總需求不必因單次成本下降而減少"],
        tensions=["單次推論變便宜，總支出卻可能變高"],
        core_question="AI 效率進步為何可能推高整體算力支出？",
        author_hypothesis="我傾向用需求彈性，而不是單次成本，解釋這個表面矛盾。",
        strongest_countercase="若應用需求已飽和，效率提升仍可能壓低總支出。",
        research_queries=[
            "AI inference cost demand elasticity",
            "Jevons paradox compute empirical data",
            "AI datacenter capex utilization",
        ],
        terms_to_explain=["Jevons paradox", "inference"],
    )


def _sources(count: int = 5) -> list[editorial_research.ResearchSource]:
    return [
        editorial_research.ResearchSource(
            title=f"Evidence {index}",
            url=f"https://source{index}.example/report",
            publisher=f"Publisher {index}",
            excerpt=("這是可供核對的延伸證據，包含方法、數據與限制。" * 8),
            evidence_role="data" if index % 2 else "countercase",
        )
        for index in range(count)
    ]


def test_deep_profiles_have_type_specific_length_and_structure() -> None:
    podcast = composer.resolve_editorial_profile("podcast")
    company = composer.resolve_editorial_profile("company")
    daily = composer.resolve_editorial_profile("morning")

    assert podcast.name == "weekly" and podcast.article_kind == "podcast"
    assert company.name == "weekly" and company.article_kind == "company"
    assert composer.word_range_for(podcast) == (4200, 6500)
    assert composer.word_range_for(company) == (3800, 6000)
    assert composer.word_range_for(daily) == (1800, 2800)


def test_podcast_research_brief_extracts_the_episode_before_web_research() -> None:
    profile = composer.resolve_editorial_profile("podcast")
    prompt = composer._build_research_brief_prompt(
        raw_title="A long interview",
        raw_content="主持人追問，來賓提出一個反直覺觀點。",
        mode="podcast",
        topic_category="ai_model",
        profile=profile,
    )

    assert "先完成 Podcast 理解" in prompt
    assert "引人入勝的摘要" in prompt
    assert "主持人的追問" in prompt and "來賓的主張" in prompt
    assert "延伸調研查詢" in prompt
    assert "不得把延伸資料當成已經完成" in prompt


def test_research_brief_queries_cover_distinct_evidence_angles() -> None:
    profile = composer.resolve_editorial_profile("podcast")
    prompt = composer._build_research_brief_prompt(
        raw_title="A long interview",
        raw_content="主持人追問，來賓提出一個反直覺觀點。",
        mode="podcast",
        topic_category="ai_model",
        profile=profile,
    )

    assert "不同研究角度" in prompt
    assert "官方或第一手" in prompt
    assert "量化或實證" in prompt
    assert "最強反方" in prompt
    assert "不可只是同義改寫" in prompt


def test_research_pack_requires_five_to_ten_distinct_usable_sources() -> None:
    with pytest.raises(editorial_research.InsufficientResearchError):
        editorial_research.validate_research_sources(_sources(4))

    duplicated = _sources(5) + [_sources(5)[0]]
    accepted = editorial_research.validate_research_sources(duplicated)
    assert len(accepted) == 5
    assert len({source.url for source in accepted}) == 5

    assert len(editorial_research.validate_research_sources(_sources(12))) == 10


@pytest.mark.parametrize("article_form", ["investigation", "argument", "self_growth"])
def test_final_writer_prompt_has_a_real_argument_shape_and_low_cognitive_load(
    article_form: str,
) -> None:
    profile = composer.resolve_editorial_profile("podcast")
    prompt = composer._build_user_prompt(
        raw_title="A long interview",
        raw_content="這段原始逐字稿不應在第二階段重新整份倒入。",
        mode="podcast",
        topic_category="ai_model",
        editorial_note="",
        profile=profile,
        research_brief=_brief(article_form),
        research_sources=_sources(),
    )

    assert "主來源萃取" in prompt
    assert "延伸證據" in prompt
    assert "作者假說" in prompt and "最強反方" in prompt
    assert "第一人稱" in prompt and "不得虛構親身經驗" in prompt
    assert "降低認知負擔" in prompt
    assert "專有名詞註解" in prompt
    assert "一節只推進一個子問題" in prompt
    assert {
        "investigation": "證據鏈",
        "argument": "論點與理由",
        "self_growth": "可實驗的行動",
    }[article_form] in prompt
    assert "這段原始逐字稿不應" not in prompt


def test_final_writer_prompt_filters_research_instead_of_dumping_it() -> None:
    profile = composer.resolve_editorial_profile("company")
    prompt = composer._build_user_prompt(
        raw_title="Example Corp",
        raw_content="財報事實。",
        mode="company",
        topic_category="us_stocks",
        editorial_note="",
        profile=profile,
        research_brief=_brief("investigation"),
        research_sources=_sources(7),
    )

    assert "5–10 個來源是作者的研究投入" in prompt
    assert "不是要全數塞進正文" in prompt
    assert "只保留會改變讀者理解或作者判斷的證據" in prompt
    assert "資訊價值閘門" in prompt
    assert "新證據、必要的因果步驟、最強反方、必要定義或讀者後果" in prompt
    assert "支持同一件事的來源合併呈現" in prompt
    assert "刪掉一段仍不影響論證" in prompt


def test_final_writer_builds_an_internal_claim_evidence_map() -> None:
    profile = composer.resolve_editorial_profile("podcast")
    prompt = composer._build_user_prompt(
        raw_title="A long interview",
        raw_content="逐字稿。",
        mode="podcast",
        topic_category="ai_model",
        editorial_note="",
        profile=profile,
        research_brief=_brief("argument"),
        research_sources=_sources(7),
    )

    assert "主張—證據圖" in prompt
    assert "拆成單一可查證斷言" in prompt
    assert "只能使用上方編號來源" in prompt
    assert "單一來源" in prompt and "衝突證據" in prompt
    assert "按子問題組織，不按來源逐篇介紹" in prompt
    assert "至少 20" not in prompt


def test_research_builder_reads_results_and_limits_domain_concentration(monkeypatch) -> None:
    class FakeDDGS:
        def text(self, query: str, max_results: int = 8):
            query_id = {
                "first evidence gap": 1,
                "second evidence gap": 2,
                "strongest countercase": 3,
            }[query]
            return [
                {
                    "title": f"{query} evidence {index}",
                    "href": f"https://publisher-{query_id}-{index}.example/report?utm_source=test",
                    "body": "搜尋摘要" * 50,
                }
                for index in range(max_results)
            ]

    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=FakeDDGS))
    from scripts import submit_source

    monkeypatch.setattr(
        submit_source,
        "_fetch_page_text",
        lambda url: "這是已讀取的正文證據，含數據、方法、限制與可供交叉驗證的細節。" * 8,
    )

    sources = editorial_research.build_research_pack(
        ["first evidence gap", "second evidence gap", "strongest countercase"]
    )

    assert len(sources) == 10
    assert all("utm_source" not in source.url for source in sources)
    assert len({source.publisher for source in sources}) == 10


def test_search_snippet_alone_does_not_count_as_read_evidence(monkeypatch) -> None:
    class FakeDDGS:
        def text(self, query: str, max_results: int = 8):
            return [
                {
                    "title": f"Unread result {index}",
                    "href": f"https://unread-{query}-{index}.example/report",
                    "body": "搜尋引擎摘要不等於已讀取證據。" * 20,
                }
                for index in range(max_results)
            ]

    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=FakeDDGS))
    from scripts import submit_source

    monkeypatch.setattr(submit_source, "_fetch_page_text", lambda url: None)

    with pytest.raises(editorial_research.InsufficientResearchError):
        editorial_research.build_research_pack(["a", "b", "c"])


@pytest.mark.asyncio
async def test_deep_writer_fails_closed_before_llm_when_research_is_missing(monkeypatch) -> None:
    async def unexpected_call(**kwargs):
        raise AssertionError("final writer must not run without a validated research pack")

    monkeypatch.setattr(composer, "call_for_json", unexpected_call)
    result = await composer.compose_substack_article(
        title="A podcast",
        content="逐字稿",
        mode="podcast",
        topic_category="ai_model",
    )

    assert result is None
