from run_pipeline import (
    MAX_POSTS_PER_SLOT,
    RECOVERY_MAX_POSTS_PER_SLOT,
    candidate_scan_limit,
)


def test_recovery_scan_budget_is_bounded(monkeypatch) -> None:
    monkeypatch.setenv("AUTOMATION_MODE", "recovery")
    assert candidate_scan_limit() == RECOVERY_MAX_POSTS_PER_SLOT == 1


def test_live_mode_preserves_legacy_scan_budget(monkeypatch) -> None:
    monkeypatch.setenv("AUTOMATION_MODE", "live")
    assert candidate_scan_limit() == MAX_POSTS_PER_SLOT == 8
