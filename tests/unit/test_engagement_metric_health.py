import asyncio

import src.engagement as engagement


class Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class Client:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def get(self, url, *, params, timeout):
        metric = params.get("metric", "basic")
        self.calls.append(metric)
        return self.responses[metric]


def metric(name, value):
    return Response(200, {"data": [{"name": name, "values": [{"value": value}]}]})


def invalid_metric():
    return Response(
        400,
        {"error": {"code": 100, "message": "(#100) The value must be a valid insights metric"}},
    )


def test_invalid_metric_is_not_zero_engagement() -> None:
    assert engagement._is_low_engagement_error(invalid_metric().json()) is False
    assert engagement._is_low_engagement_error(
        {"error": {"code": 10, "message": "Insights cannot be accessed for this post"}}
    ) is True


def test_facebook_probes_metrics_individually(monkeypatch) -> None:
    monkeypatch.setattr(engagement, "FB_PAGE_ID", "page")
    client = Client(
        {
            "basic": Response(
                200,
                {
                    "reactions": {"summary": {"total_count": 3}},
                    "comments": {"summary": {"total_count": 2}},
                },
            ),
            "post_clicks": metric("post_clicks", 14),
            "post_reactions_by_type_total": metric(
                "post_reactions_by_type_total", {"like": 3, "love": 1}
            ),
        }
    )
    result = asyncio.run(engagement.fetch_fb_insights(client, "post"))
    assert result["ok"] is True
    assert result["reach"] == 0
    assert result["views"] == 0
    assert result["clicks"] == 14
    assert result["likes"] == 3
    assert result["raw"]["insights"]["errors"] == {}
    assert client.calls == [
        "basic",
        "post_clicks",
        "post_reactions_by_type_total",
    ]


def test_facebook_reports_new_metric_contract_failure_without_faking_reach() -> None:
    client = Client(
        {
            "basic": Response(200, {"reactions": {"summary": {"total_count": 1}}}),
            "post_clicks": invalid_metric(),
            "post_reactions_by_type_total": metric(
                "post_reactions_by_type_total", {"like": 1}
            ),
        }
    )
    result = asyncio.run(engagement.fetch_fb_insights(client, "page_post"))
    assert result["ok"] is True
    assert result["reach"] == 0
    assert result["views"] == 0
    assert result["clicks"] == 0
    assert "post_clicks" in result["raw"]["insights"]["errors"]


def test_instagram_keeps_valid_metrics_when_one_is_invalid() -> None:
    client = Client(
        {
            "basic": Response(200, {"like_count": 4, "comments_count": 1}),
            "reach": metric("reach", 50),
            "saved": metric("saved", 6),
            "views": invalid_metric(),
            "total_interactions": metric("total_interactions", 12),
        }
    )
    result = asyncio.run(engagement.fetch_ig_insights(client, "ig-post"))
    assert result["ok"] is True
    assert result["reach"] == 50
    assert result["saves"] == 6
    assert result["views"] == 0
    assert result["total_interactions"] == 12
    assert "views" in result["raw"]["insights"]["errors"]
