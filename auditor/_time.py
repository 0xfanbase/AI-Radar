"""One UTC-timestamp formatter for every auditor module.

Before this helper, four near-identical ``_utcnow_iso``/``_iso_utc``
copies lived across ``auditor/`` (linkrot, missed_story,
profile_staleness, report) emitting TWO different RFC3339 shapes --
``2026-07-19T01:22:55.157733+00:00`` and ``2026-07-19T01:22:37Z`` -- so
one ``data/audit/latest.json`` mixed both formats across its own
``checked_at``/``generated_at`` fields. Every auditor timestamp now goes
through :func:`utcnow_iso` (second-precision, ``Z``-suffixed -- the shape
``report.py``'s ``generated_at``/``run_id`` derivation already
standardized on).
"""
from __future__ import annotations

from datetime import datetime, timezone


def utcnow_iso(now: datetime | None = None) -> str:
    """The current (or given) time as ``YYYY-MM-DDTHH:MM:SSZ`` in UTC."""
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
