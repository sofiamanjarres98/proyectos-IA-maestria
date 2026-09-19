from __future__ import annotations

import pandas as pd

from src.core.shifts import DEFAULT_SHIFTS
from src.core.time_segments import day_night_hours


def estimate_ops_cost(schedule: pd.DataFrame, staff: pd.DataFrame) -> pd.DataFrame:
    if schedule.empty:
        return pd.DataFrame()
    staff_lookup = staff.set_index("id").to_dict("index")
    rows = []
    for row in schedule.itertuples(index=False):
        if row.shift not in DEFAULT_SHIFTS or row.shift == "L":
            continue
        person = staff_lookup[int(row.staff_id)]
        if person["staff_type"] != "OPS":
            continue
        day_hours, night_hours = day_night_hours(row.date, DEFAULT_SHIFTS[row.shift])
        day_cost = day_hours * float(person.get("ops_day_rate") or 0)
        night_cost = night_hours * float(person.get("ops_night_rate") or 0)
        rows.append(
            {
                "staff_id": row.staff_id,
                "nombre": row.nombre,
                "fecha": row.date,
                "turno": row.shift,
                "horas_diurnas": day_hours,
                "horas_nocturnas": night_hours,
                "costo_diurno": day_cost,
                "costo_nocturno": night_cost,
                "costo_total": day_cost + night_cost,
            }
        )
    return pd.DataFrame(rows)


def ops_summary(schedule: pd.DataFrame, staff: pd.DataFrame) -> pd.DataFrame:
    detail = estimate_ops_cost(schedule, staff)
    ops_staff = staff[staff["staff_type"] == "OPS"].copy()
    rows = []
    for person in ops_staff.itertuples(index=False):
        person_detail = detail[detail["staff_id"] == int(person.id)] if not detail.empty else pd.DataFrame()
        day_hours = float(person_detail["horas_diurnas"].sum()) if not person_detail.empty else 0.0
        night_hours = float(person_detail["horas_nocturnas"].sum()) if not person_detail.empty else 0.0
        total_hours = day_hours + night_hours
        total_cost = float(person_detail["costo_total"].sum()) if not person_detail.empty else 0.0
        desired = float(person.desired_monthly_hours or 0)
        max_hours = float(person.max_monthly_hours or 0)
        rows.append(
            {
                "nombre": person.full_name,
                "horas_deseadas": desired,
                "maximo_horas": max_hours,
                "horas_asignadas": total_hours,
                "diferencia_vs_deseadas": total_hours - desired,
                "horas_diurnas": day_hours,
                "horas_nocturnas": night_hours,
                "tarifa_diurna": float(person.ops_day_rate or 0),
                "tarifa_nocturna": float(person.ops_night_rate or 0),
                "total_a_pagar_ops": total_cost,
            }
        )
    return pd.DataFrame(rows)
