from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta


@dataclass(frozen=True)
class Shift:
    code: str
    label: str
    start: time | None
    end: time | None
    color: str

    @property
    def is_work(self) -> bool:
        return self.start is not None and self.end is not None


DEFAULT_SHIFTS: dict[str, Shift] = {
    "M": Shift("M", "Manana", time(7, 0), time(13, 0), "#dbeafe"),
    "T": Shift("T", "Tarde", time(13, 0), time(19, 0), "#fde68a"),
    "N": Shift("N", "Noche", time(19, 0), time(7, 0), "#ddd6fe"),
    "C": Shift("C", "Corrido", time(7, 0), time(19, 0), "#bbf7d0"),
    "L": Shift("L", "Libre", None, None, "#f3f4f6"),
}


def shift_bounds(day: date, shift: Shift) -> tuple[datetime, datetime]:
    if not shift.is_work:
        raise ValueError("Libre has no working interval")
    start = datetime.combine(day, shift.start)
    end = datetime.combine(day, shift.end)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def shift_hours(shift: Shift) -> float:
    if not shift.is_work:
        return 0.0
    anchor = date(2000, 1, 1)
    start, end = shift_bounds(anchor, shift)
    return (end - start).total_seconds() / 3600
