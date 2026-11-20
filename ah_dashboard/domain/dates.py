"""Dates in the Netherlands, whatever the server's time zone (Docker: UTC)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta


def amsterdam_today(now: datetime | None = None) -> date:
    """Today's date in the Netherlands (AH's bonus weeks run Monday to Sunday,
    Dutch time), whatever the server's time zone is (Docker: UTC). Dutch summer
    time runs from the last Sunday of March to the last Sunday of October,
    01:00 UTC. No tz database needed."""
    now = (now or datetime.now(UTC)).astimezone(UTC)

    def last_sunday(month: int) -> datetime:
        d = date(now.year, month, 31)
        d -= timedelta(days=(d.weekday() + 1) % 7)
        return datetime(d.year, d.month, d.day, 1, tzinfo=UTC)

    offset = 2 if last_sunday(3) <= now < last_sunday(10) else 1
    return (now + timedelta(hours=offset)).date()
