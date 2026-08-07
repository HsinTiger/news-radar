import json
import sys
import types

optional_substack = types.ModuleType("substack")
optional_substack.Api = object
optional_post = types.ModuleType("substack.post")
optional_post.Post = object
sys.modules.setdefault("substack", optional_substack)
sys.modules.setdefault("substack.post", optional_post)

from substack_radar import push_pasted_draft as helper


def test_pasted_draft_push_needs_no_cover_prompts_and_sends_reader_only(
    monkeypatch,
) -> None:
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
            captured.update(payload)
            return {"id": 12345}

    class FakePost:
        def __init__(self, **kwargs):
            captured["post_kwargs"] = kwargs
            self._draft = {
                "draft_body": json.dumps(
                    {
                        "type": "doc",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "讀者正文"}],
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
            }

        def from_markdown(self, body, api=None):
            assert api is not None
            captured["markdown"] = body

        def get_draft(self):
            return dict(self._draft)

    monkeypatch.setattr(helper, "Api", FakeApi)
    monkeypatch.setattr(helper, "Post", FakePost)

    draft_id, _url = helper.push_pasted_draft(
        title="測試標題",
        subtitle="測試副標",
        body_md=(
            "讀者正文\n\n"
            "🖼 視覺位置 · internal\n\n"
            "場景描述：internal\n\n"
            "🔍 Path B · Google 搜：internal\n\n"
            "🎨 Path C · 生圖 prompt：internal\n\n"
            "讀者結尾\n\n"
            "「我專門拆解：那些你已經被市場說服、但其實正在害你的共識。」\n\n"
            "📅 每天 3 分鐘 · 舊承諾\n\n"
            "🔄 365 天複利一個眼光\n\n"
            "點此訂閱 → 不錯過下一篇拆解。\n\n"
            "📸 封面圖 Prompt · 發文前請刪除\n\ninternal cover prompt"
        ),
    )

    assert draft_id == 12345
    assert captured["post_kwargs"]["audience"] == "everyone"
    raw = captured["draft_body"]
    assert "讀者正文" in raw
    assert "每天兩篇對談延伸" in raw
    assert captured["markdown"] == "讀者正文\n\n讀者結尾"
    for forbidden in (
        "封面圖 Prompt",
        "scene_prompt",
        "concept_prompt",
        "abstract_prompt",
        "發布前刪",
    ):
        assert forbidden not in raw
