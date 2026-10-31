"""Calendar helpers: UTC month arithmetic with actual calendar hours (never a flat 720)."""

from __future__ import annotations

import calendar
from datetime import UTC, datetime

HOURS_PER_DAY = 24
DAYS_PER_YEAR = 365  # ACT/365 day count for discounting, declared in the report.


def parse_utc(text: str) -> datetime:
    """Parse an ISO-8601 UTC timestamp such as ``2026-10-01T00:00:00Z``.

    Only timezone-aware inputs are accepted; naive timestamps are rejected so that an
    ambiguous local time can never silently shift an as-of cutoff.
    """
    raw = text.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    value = datetime.fromisoformat(raw)
    if value.tzinfo is None:
        raise ValueError(f"timestamp {text!r} has no timezone; UTC offsets are required")
    return value.astimezone(UTC)


def format_utc(value: datetime) -> str:
    """Render a UTC datetime as ``YYYY-MM-DDTHH:MM:SSZ``."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def month_start(value: datetime) -> datetime:
    """Return 00:00:00 UTC on the first day of ``value``'s month."""
    value = value.astimezone(UTC)
    return datetime(value.year, value.month, 1, tzinfo=UTC)


def add_months(start: datetime, months: int) -> datetime:
    """Shift a month-start datetime by whole calendar months (negative allowed)."""
    index = start.year * 12 + (start.month - 1) + months
    year, month0 = divmod(index, 12)
    return datetime(year, month0 + 1, 1, tzinfo=UTC)


def hours_in_month(start: datetime) -> int:
    """Actual UTC hours in the calendar month beginning at ``start``.

    October 2026 has 744 hours, November 2026 has 720, February 2027 has 672.
    """
    days = calendar.monthrange(start.year, start.month)[1]
    return days * HOURS_PER_DAY


def is_month_start(value: datetime) -> bool:
    """True when ``value`` is exactly 00:00:00 UTC on the first day of a month."""
    return value == month_start(value)


def months_between(start: datetime, end: datetime) -> list[datetime]:
    """Month starts covering ``[start, end)`` where both are month-aligned."""
    if not (is_month_start(start) and is_month_start(end)):
        raise ValueError("delivery windows must be aligned to UTC calendar month boundaries")
    if end <= start:
        raise ValueError("delivery_end must be after delivery_start")
    out: list[datetime] = []
    cursor = start
    while cursor < end:
        out.append(cursor)
        cursor = add_months(cursor, 1)
    return out


def hours_between(start: datetime, end: datetime) -> int:
    """Whole hours in ``[start, end)``; both instants must be on an hour boundary."""
    delta = end - start
    seconds = int(delta.total_seconds())
    if seconds <= 0:
        raise ValueError("end must be after start")
    if seconds % 3600:
        raise ValueError("delivery windows must start and end on an hour boundary")
    return seconds // 3600


def year_fraction(start: datetime, end: datetime) -> float:
    """ACT/365 year fraction between two instants (may be negative)."""
    return (end - start).total_seconds() / (DAYS_PER_YEAR * HOURS_PER_DAY * 3600)


def month_label(start: datetime) -> str:
    """``YYYY-MM`` label for a month start."""
    return start.strftime("%Y-%m")
