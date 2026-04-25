"""
Unit tests for src.rate_limit.can_call().

Per dashboard agent spec — 8 cases:
  1. empty call_times → allowed
  2. 199-of-200 IG → allowed
  3. 200-of-200 IG, oldest 20 min ago → blocked, ~40 min wait
  4. 250-of-250 Threads, oldest 23 h ago → blocked, ~1 h wait
  5. unknown platform → ValueError
  6. naive `now` → ValueError
  7. unsorted call_times → still correct (internal sort)
  8. window-rolloff edge: oldest just past window → allowed
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.rate_limit import RATE_LIMITS, can_call


UTC = timezone.utc
NOW = datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)


# ---------- 1. empty list -------------------------------------------------
def test_empty_call_times_allows():
    allowed, wait = can_call("instagram", NOW, [])
    assert allowed is True
    assert wait == 0


# ---------- 2. just under limit ------------------------------------------
def test_199_of_200_ig_allows():
    # 199 calls ALL within the 1h window (use seconds spacing to stay in window)
    times = [NOW - timedelta(seconds=i + 1) for i in range(199)]  # 1s..199s ago
    allowed, wait = can_call("instagram", NOW, times)
    assert allowed is True
    assert wait == 0


# ---------- 3. at limit, IG, oldest 20m ago -------------------------------
def test_200_of_200_ig_blocked_until_oldest_rolloff():
    # 200 calls all within 1h. Oldest = 20 min ago → rolls off at +60 min
    # from oldest = 40 min from now. Other 199 calls spaced 1s..199s ago
    # (all in window).
    oldest = NOW - timedelta(minutes=20)
    others = [NOW - timedelta(seconds=i + 1) for i in range(199)]
    times = [oldest] + others
    allowed, wait = can_call("instagram", NOW, times)
    assert allowed is False, (
        f"expected blocked but got allowed=True; "
        f"len(times)={len(times)}, oldest={oldest.isoformat()}"
    )
    # ~40 min wait; allow ±2s for floor-vs-ceil
    expected = 40 * 60
    assert abs(wait - expected) <= 2, f"expected ~{expected}s, got {wait}s"


# ---------- 4. Threads 250-of-250, oldest 23h ago -------------------------
def test_250_of_250_threads_blocked_until_oldest_rolloff():
    oldest = NOW - timedelta(hours=23)  # rolls off at +24h from itself = +1h from now
    times = [oldest] + [
        NOW - timedelta(minutes=i + 1) for i in range(249)
    ]
    allowed, wait = can_call("threads", NOW, times)
    assert allowed is False
    expected = 60 * 60
    assert abs(wait - expected) <= 2, f"expected ~{expected}s, got {wait}s"


# ---------- 5. unknown platform → ValueError ------------------------------
def test_unknown_platform_raises():
    with pytest.raises(ValueError, match="Unknown platform"):
        can_call("tiktok", NOW, [])


# ---------- 6. naive `now` → ValueError -----------------------------------
def test_naive_now_raises():
    naive = datetime(2026, 4, 25, 12, 0, 0)  # no tzinfo
    with pytest.raises(ValueError, match="tz-aware"):
        can_call("instagram", naive, [])


def test_naive_call_time_raises():
    naive_call = datetime(2026, 4, 25, 11, 0, 0)  # no tzinfo
    with pytest.raises(ValueError, match="tz-aware"):
        can_call("instagram", NOW, [naive_call])


# ---------- 7. unsorted input → still correct -----------------------------
def test_unsorted_call_times_still_correct():
    # Same as test 3 (200 calls, oldest 20min ago, blocked) but shuffled.
    # All 199 'rest' entries within 1h window via seconds offsets.
    oldest = NOW - timedelta(minutes=20)
    rest = [NOW - timedelta(seconds=i + 1) for i in range(199)]
    # Put oldest in the middle to confirm internal sort matters
    times = rest[:50] + [oldest] + rest[50:]
    allowed, wait = can_call("instagram", NOW, times)
    assert allowed is False, f"got allowed=True with len(times)={len(times)}"
    expected = 40 * 60
    assert abs(wait - expected) <= 2


# ---------- 8. window-rolloff edge: oldest just past window → allowed ----
def test_oldest_just_past_window_allows():
    # 200 calls, oldest 60min and 1s ago → that one is OUT of window,
    # leaves 199 in-window → allowed.
    oldest_out = NOW - timedelta(minutes=60, seconds=1)
    rest = [NOW - timedelta(minutes=i + 1) for i in range(199)]  # all inside 1h
    times = [oldest_out] + rest
    allowed, wait = can_call("instagram", NOW, times)
    assert allowed is True
    assert wait == 0


# ---------- bonus: limit table sanity -------------------------------------
def test_rate_limits_table_shape():
    """Catch typos / accidental edits to RATE_LIMITS."""
    assert set(RATE_LIMITS) == {"instagram", "facebook", "threads"}
    for plat, (limit, window_sec) in RATE_LIMITS.items():
        assert isinstance(limit, int) and limit > 0
        assert isinstance(window_sec, int) and window_sec > 0
