from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Holiday:
    date: date
    name: str
    source: str = "Ley 51 de 1983"


def easter_date(year: int) -> date:
    """Return Gregorian Easter Sunday using Meeus/Jones/Butcher algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def next_monday(value: date) -> date:
    days = (7 - value.weekday()) % 7
    return value + timedelta(days=days)


def colombia_holidays(year: int) -> list[Holiday]:
    easter = easter_date(year)
    fixed = [
        Holiday(date(year, 1, 1), "Ano nuevo"),
        Holiday(next_monday(date(year, 1, 6)), "Reyes Magos"),
        Holiday(next_monday(date(year, 3, 19)), "San Jose"),
        Holiday(date(year, 5, 1), "Dia del trabajo"),
        Holiday(next_monday(date(year, 6, 29)), "San Pedro y San Pablo"),
        Holiday(date(year, 7, 20), "Independencia de Colombia"),
        Holiday(date(year, 8, 7), "Batalla de Boyaca"),
        Holiday(next_monday(date(year, 8, 15)), "Asuncion de la Virgen"),
        Holiday(next_monday(date(year, 10, 12)), "Dia de la Raza"),
        Holiday(next_monday(date(year, 11, 1)), "Todos los Santos"),
        Holiday(next_monday(date(year, 11, 11)), "Independencia de Cartagena"),
        Holiday(date(year, 12, 8), "Inmaculada Concepcion"),
        Holiday(date(year, 12, 25), "Navidad"),
    ]
    movable = [
        Holiday(easter - timedelta(days=3), "Jueves Santo"),
        Holiday(easter - timedelta(days=2), "Viernes Santo"),
        Holiday(next_monday(easter + timedelta(days=39)), "Ascension del Senor"),
        Holiday(next_monday(easter + timedelta(days=60)), "Corpus Christi"),
        Holiday(next_monday(easter + timedelta(days=68)), "Sagrado Corazon"),
    ]
    return sorted(fixed + movable, key=lambda h: h.date)
