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
    assert result.inputs["submission_id"] == "s1"


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
