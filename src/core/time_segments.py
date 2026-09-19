from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .shifts import Shift, shift_bounds


def _next_boundary(moment: datetime, night_start: time, night_end: time) -> datetime:
    candidates = []
    for offset in (0, 1):
        current_date = moment.date() + timedelta(days=offset)
        candidates.append(datetime.combine(current_date, night_start))
        candidates.append(datetime.combine(current_date, night_end))
    future = [candidate for candidate in candidates if candidate > moment]
    return min(future)


def is_night(moment: datetime, night_start: time, night_end: time) -> bool:
    current = moment.time()
    if night_start < night_end:
        return night_start <= current < night_end
    return current >= night_start or current < night_end


def split_shift(
    day: date,
    shift: Shift,
    night_start: time = time(19, 0),
    night_end: time = time(6, 0),
) -> list[dict]:
    if not shift.is_work:
        return []
    start, end = shift_bounds(day, shift)
    cursor = start
    segments = []
    while cursor < end:
        boundary = min(_next_boundary(cursor, night_start, night_end), end)
        hours = (boundary - cursor).total_seconds() / 3600
        segments.append(
            {
                "start": cursor,
                "end": boundary,
                "hours": hours,
                "bucket": "NOCTURNA" if is_night(cursor, night_start, night_end) else "DIURNA",
                "calendar_date": cursor.date(),
            }
        )
        cursor = boundary
    return segments


def day_night_hours(day: date, shift: Shift) -> tuple[float, float]:
    day_hours = 0.0
    night_hours = 0.0
    for segment in split_shift(day, shift):
        if segment["bucket"] == "NOCTURNA":
            night_hours += segment["hours"]
        else:
            day_hours += segment["hours"]
    return day_hours, night_hours
