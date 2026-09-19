from __future__ import annotations

import calendar
from datetime import date

import pandas as pd

from src.core.shifts import DEFAULT_SHIFTS, shift_hours


SHIFT_COLORS = {
    "M": "#bfdbfe",
    "T": "#fde68a",
    "N": "#c4b5fd",
    "C": "#86efac",
    "L": "#e5e7eb",
    "VAC": "#bbf7d0",
    "INC": "#fecaca",
    "LIC": "#fed7aa",
    "PER": "#bae6fd",
    "BLQ": "#fca5a5",
}


def make_summary(schedule: pd.DataFrame, staff: pd.DataFrame, holidays: set[date]) -> pd.DataFrame:
    if schedule.empty:
        return pd.DataFrame()
    rows = []
    for (staff_id, nombre, tipo), group in schedule.groupby(["staff_id", "nombre", "tipo"]):
        hours = 0.0
        nights = int((group["shift"] == "N").sum())
        weekends = int(group[(group["date"].map(lambda d: d.weekday() >= 5)) & (group["shift"] != "L")].shape[0])
        holiday_count = int(group[(group["date"].isin(holidays)) & (group["shift"] != "L")].shape[0])
        for item in group.itertuples(index=False):
            hours += shift_hours(DEFAULT_SHIFTS.get(item.shift, DEFAULT_SHIFTS["L"]))
        rows.append(
            {
                "staff_id": staff_id,
                "nombre": nombre,
                "tipo": tipo,
                "horas": hours,
                "noches": nights,
                "fines_semana": weekends,
                "festivos": holiday_count,
            }
        )
    return pd.DataFrame(rows).sort_values(["tipo", "nombre"])


def schedule_pivot(schedule: pd.DataFrame) -> pd.DataFrame:
    if schedule.empty:
        return pd.DataFrame()
    pivot = schedule.pivot_table(index=["staff_id", "tipo", "nombre"], columns="dia", values="shift", aggfunc="first")
    return pivot.reset_index()


def render_calendar_html(pivot: pd.DataFrame, holidays: set[date], year: int, month: int) -> str:
    if pivot.empty:
        return "<p>No hay calendario generado.</p>"
    day_cols = [col for col in pivot.columns if isinstance(col, int) or str(col).isdigit()]
    html = [
        "<style>",
        ".cal{border-collapse:collapse;width:100%;font-size:13px}.cal th,.cal td{border:1px solid #d1d5db;padding:5px;text-align:center}",
        ".cal th{background:#f9fafb;position:sticky;top:0}.name{text-align:left!important;white-space:nowrap;font-weight:600}",
        "</style><table class='cal'><thead><tr><th>Tipo</th><th>Nombre</th>",
    ]
    for day in day_cols:
        current = date(year, month, int(day))
        label = f"{day}<br>{calendar.day_abbr[current.weekday()]}"
        if current in holidays:
            label += "<br>Fest."
        html.append(f"<th>{label}</th>")
    html.append("</tr></thead><tbody>")
    for _, row in pivot.iterrows():
        html.append(f"<tr><td>{row['tipo']}</td><td class='name'>{row['nombre']}</td>")
        for day in day_cols:
            value = row[day]
            color = SHIFT_COLORS.get(value, "#fee2e2")
            html.append(f"<td style='background:{color}'>{value}</td>")
        html.append("</tr>")
    html.append("</tbody></table>")
    return "".join(html)
