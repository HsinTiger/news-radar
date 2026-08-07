import sys
import types

from substack_radar import compose, composer


def test_writer_names_the_reader_before_applying_style() -> None:
    podcast_brief = composer.load_editorial_brief(
        composer.resolve_editorial_profile("podcast")
    )
    company_brief = composer.load_editorial_brief(
        composer.resolve_editorial_profile("company")
    )

    for brief in (podcast_brief, company_brief):
        assert "沒時間追完整的國外 Podcast、財報與社群資訊" in brief
        assert "篩選、查證" in brief
        assert "台灣繁體中文" in brief
        assert "不假設是領域專家" in brief

    assert "科技、商業與 AI 的知識工作者" in podcast_brief
    assert "商業模式、財務證據、風險與長期訊號" in company_brief
    assert "短線喊單" in company_brief


def test_automated_draft_ignores_stale_paid_audience_env(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("SUBSTACK_AUTO_DRAFT", "1")
    monkeypatch.setenv("SUBSTACK_COOKIES_STRING", "cookie")
    monkeypatch.setenv("SUBSTACK_PUBLICATION_URL", "https://example.substack.com")
    monkeypatch.setenv("SUBSTACK_AUDIENCE", "only_paid")
    captured = {}

    class FakeApi:
        def __init__(self, **_kwargs):
            pass

        def get_user_id(self):
            return 7

        def post_draft(self, payload):
            captured["payload"] = payload
            return {"id": 12345}

    class FakePost:
        def __init__(self, **kwargs):
            captured["post_kwargs"] = kwargs

        def from_markdown(self, _body, api=None):
            assert api is not None

        def get_draft(self):
            return {"draft_body": "{}"}

    fake_substack = types.ModuleType("substack")
    fake_substack.Api = FakeApi
    fake_post = types.ModuleType("substack.post")
    fake_post.Post = FakePost
    monkeypatch.setitem(sys.modules, "substack", fake_substack)
    monkeypatch.setitem(sys.modules, "substack.post", fake_post)

    article_path = tmp_path / "Article_Substack.md"
    article_path.write_text(
        "# 測試標題\n\n*測試副標*\n\n這是要給免費讀者閱讀的正文。",
        encoding="utf-8",
    )

    draft_id = compose.push_to_substack_draft(
        article_md_path=article_path,
        title="測試標題",
        subtitle="測試副標",
    )

    assert draft_id == 12345
    assert captured["post_kwargs"]["audience"] == "everyone"
