"""
News Radar · Rate Limiter (pure function)
==========================================

Used by `src.engagement.sync_bucket_polls` to gate per-platform Meta Graph
API calls so we don't trip rate limits.

Limits (best-effort, adjust based on real production data):
  - Instagram Graph API:      200 calls / 1 hour
  - Facebook Graph API:       200 calls / 1 hour
  - Threads Graph API:        250 calls / 24 hours

`can_call` is a pure function — no IO, no global state. Caller passes in
the list of recent successful call timestamps (from in-memory tracker or
DB log), function returns whether next call is allowed.

Design notes:
  - Sliding window count (not token bucket): simple to reason about, matches
    Meta's documented quota model.
  - All datetimes must be tz-aware UTC. naive → ValueError (defensive — we
    found one app-wide naive bug already; treat consistently).
  - `call_times` may be unsorted; we sort internally before window math.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal, Sequence


# ---- Per-platform sliding window quotas -----------------------------------
# (max_calls, window_seconds)
RATE_LIMITS: dict[str, tuple[int, int]] = {
    "instagram": (200, 60 * 60),         # 200 / 1h
    "facebook":  (200, 60 * 60),         # 200 / 1h
    "threads":   (250, 24 * 60 * 60),    # 250 / 24h (the binding constraint)
}

PlatformLiteral = Literal["instagram", "facebook", "threads"]


def can_call(
    platform: str,
    now: datetime,
    call_times: Sequence[datetime],
) -> tuple[bool, int]:
    """Decide whether the next API call to `platform` is allowed *right now*.

    Args:
        platform:    one of "instagram" / "facebook" / "threads".
                     Unknown → ValueError (defensive — typos shouldn't pass).
        now:         tz-aware UTC datetime; naive → ValueError.
        call_times:  iterable of tz-aware UTC datetimes — successful (or
                     attempted) calls so far. Order doesn't matter; sorted
                     internally. Each entry must be tz-aware; naive → ValueError.

    Returns:
        (allowed, seconds_until_next_ok).
        allowed=True  → seconds_until_next_ok=0
        allowed=False → seconds_until_next_ok = max(1, secs_to_oldest_rolloff)

    The +1 second floor on the wait estimate covers off-by-one edge cases
    near the window boundary (`now == oldest + window` is technically
    eligible but the next can_call() in the same instant might race).
    """
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("now must be a tz-aware UTC datetime")
    if platform not in RATE_LIMITS:
        raise ValueError(
            f"Unknown platform {platform!r}; "
            f"expected one of {sorted(RATE_LIMITS)}"
        )

    limit, window_sec = RATE_LIMITS[platform]
    window = timedelta(seconds=window_sec)

    # Filter to in-window calls, validating each entry is tz-aware.
    in_window: list[datetime] = []
    for t in call_times:
        if not isinstance(t, datetime) or t.tzinfo is None:
            raise ValueError(
                f"call_times entries must be tz-aware UTC datetimes; got {t!r}"
            )
        if (now - t) < window:
            in_window.append(t)

    in_window.sort()  # robust to unsorted input

    if len(in_window) < limit:
        return (True, 0)

    # At limit. Next call allowed when the oldest in-window call rolls off.
    oldest = in_window[0]
    rolloff_at = oldest + window
    secs = max(1, int((rolloff_at - now).total_seconds()) + 1)
    return (False, secs)
