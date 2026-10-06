from scripts.submission_dispatch import build_dispatch


def test_substack_priority_maps_to_draft_workflow() -> None:
    result = build_dispatch(
        {
            "id": "s1",
            "target": "substack",
            "source_type": "text",
            "content": "body",
            "note": "title",
            "platforms": [],
            "mode": "draft_priority",
        }
    )
    assert result.workflow == "substack-submit.yml"
    assert result.inputs["immediate"] == "true"
    assert result.inputs["publish_now"] == "false"
    assert result.inputs["submission_id"] == "s1"


def test_substack_publish_now_reaches_the_mac_workflow() -> None:
    result = build_dispatch(
        {
            "id": "submission-publish-12345678",
            "target": "substack",
            "source_type": "youtube",
            "content": "https://youtube.com/watch?v=example",
            "note": "延伸核心交鋒",
            "platforms": [],
            "mode": "publish_now",
        }
    )
    assert result.workflow == "substack-submit.yml"
    assert result.inputs["immediate"] == "true"
    assert result.inputs["publish_now"] == "true"


def test_meta_text_publish_now_maps_platform_names() -> None:
    result = build_dispatch(
        {
            "id": "m1",
            "target": "meta",
            "source_type": "text",
            "content": "body",
            "note": "title",
            "platforms": ["facebook", "threads"],
            "mode": "publish_now",
        }
    )
    assert result.workflow == "publish_now.yml"
    assert result.inputs["platforms"] == "fb,threads"
    assert result.inputs["text"] == "body"
    assert result.inputs["setup_only"] == "false"
    assert result.inputs["report_submission_state"] == "true"


def test_meta_queue_uses_submit_source() -> None:
    result = build_dispatch(
        {
            "id": "m2",
            "target": "meta",
            "source_type": "url",
            "content": "https://example.com",
            "note": "",
            "platforms": ["instagram"],
            "mode": "queue",
        }
    )
    assert result.workflow == "submit-source.yml"
    assert result.inputs["platforms"] == "ig"


def test_regenerated_variant_is_immediate_draft_only_and_marked_variant():
    """儀表板重產（draft_variant）：立刻寫、獨立新稿、絕不自動公開。"""
    from scripts.submission_dispatch import build_dispatch

    base = {"id": "11111111-2222", "target": "substack", "source_type": "youtube",
            "content": "https://youtu.be/abc", "note": "新提示"}
    variant = build_dispatch({**base, "mode": "draft_variant"})
    assert variant.workflow == "substack-submit.yml"
    assert variant.inputs["immediate"] == "true"
    assert variant.inputs["publish_now"] == "false"
    assert variant.inputs["variant"] == "true"
    plain = build_dispatch({**base, "mode": "draft_priority"})
    assert plain.inputs["variant"] == "false"
