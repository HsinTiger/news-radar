"""Substack delivery-audience contract.

Drafts are free-reader first. A non-default audience must be supplied by an
explicit caller argument; ambient environment state is never an owner choice.
"""

from __future__ import annotations


DEFAULT_SUBSTACK_AUDIENCE = "everyone"
VALID_SUBSTACK_AUDIENCES = frozenset(
    {"everyone", "only_free", "only_paid", "founding"}
)


def validate_substack_audience(audience: str) -> str:
    """Return a supported explicit audience or fail before remote mutation."""
    if audience not in VALID_SUBSTACK_AUDIENCES:
        allowed = ", ".join(sorted(VALID_SUBSTACK_AUDIENCES))
        raise ValueError(f"unsupported Substack audience {audience!r}; use {allowed}")
    return audience
