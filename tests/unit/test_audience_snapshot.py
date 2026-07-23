import httpx

from scripts.audience_snapshot import _metric_value, collect


def test_metric_value_supports_graph_and_threads_shapes() -> None:
    assert _metric_value({"followers_count": 123}, "followers_count") == 123
    assert _metric_value(
        {"data": [{"name": "followers_count", "total_value": {"value": 456}}]},
        "followers_count",
    ) == 456
    assert _metric_value(
        {"data": [{"name": "followers_count", "values": [{"value": 789}]}]},
        "followers_count",
    ) == 789


def test_collect_preserves_platform_specific_health() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "threads.net" in str(request.url):
            return httpx.Response(400, json={"error": {"code": 100, "message": "metric unavailable"}})
        if "ig" in request.url.params.get("access_token", ""):
            return httpx.Response(200, json={"followers_count": 20})
        return httpx.Response(200, json={"fan_count": 10})

    env = {
        "FB_PAGE_ID": "fb",
        "FB_PAGE_ACCESS_TOKEN": "fb-token",
        "IG_BUSINESS_ACCOUNT_ID": "ig",
        "IG_ACCESS_TOKEN": "ig-token",
        "THREADS_USER_ID": "threads",
        "THREADS_ACCESS_TOKEN": "threads-token",
    }
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        payload = collect(client, env)
    followers = {row["platform"]: row["followers"] for row in payload["audience"]}
    health = {row["platform"]: row["status"] for row in payload["health"]}
    assert followers == {"facebook": 10, "instagram": 20}
    assert health == {"facebook": "healthy", "instagram": "healthy", "threads": "degraded"}
