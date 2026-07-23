from scripts.metric_health_canary import classify_result


def test_canary_classifies_healthy_native_metrics() -> None:
    status, detail = classify_result(
        {"ok": True, "views": 100, "likes": 3, "raw": {"data": []}}
    )
    assert status == "healthy"
    assert "nonzero=views,likes" in detail or "nonzero=likes,views" in detail


def test_canary_preserves_individual_metric_failure() -> None:
    status, detail = classify_result(
        {
            "ok": True,
            "reach": 50,
            "raw": {"insights": {"errors": {"post_impressions": {"error": {"code": 100}}}}},
        }
    )
    assert status == "degraded"
    assert "post_impressions" in detail


def test_canary_marks_failed_fetch_as_error() -> None:
    status, _ = classify_result({"ok": False, "raw": {"error": {"code": 190}}})
    assert status == "error"
