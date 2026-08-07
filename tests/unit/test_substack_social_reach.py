from substack_radar import composer, editorial_research


def _research_source(index: int) -> editorial_research.ResearchSource:
    return editorial_research.ResearchSource(
        title=f"Durable evidence {index}",
        url=f"https://publisher-{index}.example/report",
        publisher=f"publisher-{index}.example",
        excerpt="這是已經讀取、可供核對的耐久來源，包含數據、方法、限制與反方。" * 8,
        evidence_role="data" if index % 2 else "countercase",
    )


def _brief() -> composer.EditorialResearchBrief:
    return composer.EditorialResearchBrief(
        article_form="argument",
        source_digest="這段主來源已經先被消化，保留真正的分歧、必要背景與尚未解決的問題。" * 3,
        compelling_exchange="主持人追問成本下降是否真的會降低總支出，來賓提出相反的需求彈性解釋。",
        source_claims=["單位成本下降", "總需求可能增加"],
        tensions=["效率提升與總支出增加可能同時發生"],
        core_question="模型效率提高為何仍可能推升總算力需求？",
        author_hypothesis="我傾向先檢查需求彈性，而不是直接把單位成本當成總支出的答案。",
        strongest_countercase="若需求已經飽和，效率提升仍可能直接降低總支出與算力價格。",
        research_queries=["AI inference demand elasticity", "compute price evidence", "countercase"],
        terms_to_explain=["需求彈性"],
    )


def test_public_social_reach_separates_attributed_claim_from_discovery_only() -> None:
    def searcher(query: str, max_results: int = 4):
        assert max_results == 4
        if query.startswith("site:reddit.com"):
            return [
                {
                    "title": "Operators discuss inference demand",
                    "href": "https://www.reddit.com/r/MachineLearning/comments/abc/operators/",
                    "body": "A community discussion about measured inference demand.",
                }
            ]
        if query.startswith("site:x.com"):
            return [
                {
                    "title": "Engineer thread on compute pricing",
                    "href": "https://x.com/example/status/123",
                    "body": "A search snippet that must not become evidence.",
                }
            ]
        return []

    def reader(url: str):
        if url.startswith("https://old.reddit.com/"):
            return "Reddit 公開頁面可讀取的具名主張與討論內容。" * 12
        return None

    report = editorial_research.collect_social_reach(
        ["AI inference demand"],
        searcher=searcher,
        reader=reader,
    )

    by_platform = {signal.platform: signal for signal in report.signals}
    assert by_platform["reddit"].evidence_status == "attributed_claim"
    assert by_platform["reddit"].access_method == "public_page"
    assert by_platform["reddit"].url.startswith("https://reddit.com/")
    assert by_platform["x"].evidence_status == "discovery_only"
    assert by_platform["x"].access_method == "public_search"
    assert report.health == {"reddit": "available_public", "x": "lead_only"}
    assert all("site:" not in query for query in report.upstream_queries)
    assert "cookie" not in report.model_dump_json().lower()


def test_research_bundle_uses_social_leads_but_not_as_evidence(monkeypatch) -> None:
    signal = editorial_research.SocialSignal(
        platform="x",
        title="Engineer thread on compute pricing",
        url="https://x.com/example/status/123",
        excerpt="Only a discovery snippet.",
        access_method="public_search",
        evidence_status="discovery_only",
    )
    reach = editorial_research.SocialReachReport(
        signals=[signal],
        health={"reddit": "unavailable", "x": "lead_only"},
        upstream_queries=["Engineer thread on compute pricing"],
    )
    captured = {}

    monkeypatch.setattr(
        editorial_research,
        "collect_social_reach",
        lambda _queries: reach,
    )

    def fake_build(queries, **_kwargs):
        captured["queries"] = list(queries)
        return [_research_source(index) for index in range(5)]

    monkeypatch.setattr(editorial_research, "build_research_pack", fake_build)

    bundle = editorial_research.build_research_bundle(["base evidence query"])

    assert captured["queries"] == [
        "base evidence query",
        "Engineer thread on compute pricing",
    ]
    assert len(bundle.evidence_sources) == 5
    assert all("x.com" not in source.url for source in bundle.evidence_sources)
    assert bundle.social_reach.signals == [signal]


def test_generic_x_search_title_uses_readable_post_text_for_upstream_query() -> None:
    def searcher(query: str, max_results: int = 4):
        if query.startswith("site:x.com"):
            return [
                {
                    "title": "x.com",
                    "href": "https://x.com/operator/status/456",
                    "body": "generic snippet",
                }
            ]
        return []

    report = editorial_research.collect_social_reach(
        ["frontier model serving cost"],
        searcher=searcher,
        reader=lambda _url: (
            "Frontier model serving cost includes inference clusters and memory capacity. "
            "The post links to a reproducible cost model with explicit assumptions."
        ),
    )

    assert report.upstream_queries
    assert report.upstream_queries[0].startswith("Frontier model serving cost")
    assert "x.com" not in report.upstream_queries


def test_social_search_title_is_bounded_before_model_validation() -> None:
    long_title = "a16z on X: " + ("open source AI infrastructure " * 20)

    def searcher(query: str, max_results: int = 4):
        if query.startswith("site:x.com"):
            return [
                {
                    "title": long_title,
                    "href": "https://x.com/a16z/status/789",
                    "body": "A public search snippet about open source AI infrastructure.",
                }
            ]
        return []

    report = editorial_research.collect_social_reach(
        ["open source AI infrastructure"],
        searcher=searcher,
        reader=lambda _url: None,
    )

    assert len(report.signals) == 1
    assert report.signals[0].title == long_title[:240]
    assert len(report.signals[0].title) == 240
    assert report.signals[0].evidence_status == "discovery_only"


def test_deep_writer_prompt_keeps_social_claims_below_evidence() -> None:
    report = editorial_research.SocialReachReport(
        signals=[
            editorial_research.SocialSignal(
                platform="reddit",
                title="Operators discuss inference demand",
                url="https://reddit.com/r/MachineLearning/comments/abc/operators",
                excerpt="Reddit 公開頁面可讀取的具名主張與討論內容。" * 4,
                access_method="public_page",
                evidence_status="attributed_claim",
            ),
            editorial_research.SocialSignal(
                platform="x",
                title="Engineer thread on compute pricing",
                url="https://x.com/example/status/123",
                excerpt="A search snippet that must not become evidence.",
                access_method="public_search",
                evidence_status="discovery_only",
            ),
        ],
        health={"reddit": "available_public", "x": "lead_only"},
        upstream_queries=[],
    )

    prompt = composer._build_user_prompt(
        raw_title="A podcast",
        raw_content="逐字稿不應重新倒入第二階段。",
        mode="podcast",
        topic_category="ai_model",
        editorial_note="",
        profile=composer.resolve_editorial_profile("podcast"),
        research_brief=_brief(),
        research_sources=[_research_source(index) for index in range(5)],
        social_reach=report,
    )

    assert "社群觸達" in prompt
    assert "attributed_claim" in prompt and "discovery_only" in prompt
    assert "只能具名呈現為某人或社群的主張" in prompt
    assert "不得引用、改寫成事實或暗示已讀原文" in prompt
    assert "不能取代上方 5–10 個延伸證據" in prompt
    assert "按讚、轉貼、留言數" in prompt
