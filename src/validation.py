from __future__ import annotations

from datetime import timedelta

import pandas as pd

from src.core.holidays_colombia import colombia_holidays
from src.core.shifts import DEFAULT_SHIFTS, shift_hours


def validate_schedule(
    schedule: pd.DataFrame,
    staff: pd.DataFrame,
    demand: pd.DataFrame,
    availability: pd.DataFrame,
    novedades: pd.DataFrame,
    *,
    max_shift_hours: float = 12,
) -> pd.DataFrame:
    alerts = []
    if schedule.empty:
        return pd.DataFrame(
            [{"severity": "ERROR", "rule": "SIN_CALENDARIO", "message": "No hay calendario para validar."}]
        )
    staff_lookup = staff.set_index("id").to_dict("index")
    availability_lookup = {
        (int(row.staff_id), row.date, row.shift): bool(row.available)
        for row in availability.itertuples(index=False)
    }
    holidays = {holiday.date for holiday in colombia_holidays(schedule["date"].min().year)}

    for row in schedule.itertuples(index=False):
        if row.shift not in DEFAULT_SHIFTS or row.shift == "L":
            continue
        if shift_hours(DEFAULT_SHIFTS[row.shift]) > max_shift_hours:
            alerts.append(
                {
                    "severity": "ERROR",
                    "rule": "TURNO_MAYOR_12H",
                    "staff": row.nombre,
                    "date": row.date,
                    "message": f"El turno {row.shift} supera el maximo configurado de {max_shift_hours} horas.",
                    "what_to_do": "Cambiar ese turno o documentar una excepcion autorizada.",
                }
            )
        person = staff_lookup[int(row.staff_id)]
        if person["staff_type"] == "OPS" and not availability_lookup.get((int(row.staff_id), row.date, row.shift), False):
            alerts.append(
                {
                    "severity": "ERROR",
                    "rule": "OPS_SIN_DISPONIBILIDAD",
                    "staff": row.nombre,
                    "date": row.date,
                    "message": "Asignacion OPS por fuera de disponibilidad ofrecida.",
                    "what_to_do": "Cambiar a L o confirmar que el OPS acepto ese turno.",
                }
            )
        if not novedades.empty:
            block = novedades[
                (novedades["staff_id"] == int(row.staff_id))
                & (novedades["start_date"] <= row.date)
                & (novedades["end_date"] >= row.date)
                & (novedades["blocks_assignment"] == True)
            ]
            if not block.empty:
                alerts.append(
                    {
                        "severity": "ERROR",
                        "rule": "NOVEDAD_BLOQUEANTE",
                        "staff": row.nombre,
                        "date": row.date,
                        "message": f"Asignacion durante {block.iloc[0]['event_type']}.",
                        "what_to_do": "Quitar el turno o corregir la novedad si esta mal cargada.",
                    }
                )

    grouped = schedule[schedule["shift"].isin(["M", "T", "N", "C"])].groupby(["date", "shift"]).size().reset_index(name="assigned")
    merged = demand.merge(grouped, on=["date", "shift"], how="left")
    merged["assigned"] = merged["assigned"].fillna(0).astype(int)
    for row in merged.itertuples(index=False):
        if int(row.assigned) != int(row.required):
            alerts.append(
                {
                    "severity": "ERROR",
                    "rule": "COBERTURA",
                    "staff": "",
                    "date": row.date,
                    "message": f"Turno {row.shift}: requeridos {row.required}, asignados {row.assigned}.",
                    "what_to_do": "Asignar o retirar medicos hasta que el numero coincida con la demanda.",
                }
            )

    for staff_id, person_schedule in schedule.sort_values("date").groupby("staff_id"):
        person_schedule = person_schedule.reset_index(drop=True)
        for idx, row in person_schedule.iterrows():
            if row["shift"] != "N":
                continue
            next_day = row["date"] + timedelta(days=1)
            next_rows = person_schedule[person_schedule["date"] == next_day]
            if not next_rows.empty and next_rows.iloc[0]["shift"] in ("M", "T", "C"):
                alerts.append(
                    {
                        "severity": "ERROR",
                        "rule": "POSTURNO",
                        "staff": row["nombre"],
                        "date": next_day,
                        "message": "Turno incompatible despues de noche.",
                        "what_to_do": "Dejar libre el dia siguiente o usar un turno compatible.",
                    }
                )
        real_free_days = person_schedule[person_schedule["shift"].isin(["L", "VAC", "INC", "LIC", "PER", "BLQ"])]["date"].tolist()
        pure_free = 0
        night_dates = set(person_schedule[person_schedule["shift"] == "N"]["date"])
        for free_day in real_free_days:
            if free_day - timedelta(days=1) not in night_dates:
                pure_free += 1
        if pure_free < 1:
            alerts.append(
                {
                    "severity": "ADVERTENCIA",
                    "rule": "SIN_LIBRE_REAL",
                    "staff": person_schedule.iloc[0]["nombre"],
                    "date": "",
                    "message": "No tiene al menos un dia libre real en el mes que no sea posturno.",
                    "what_to_do": "Intentar asignar un dia L que no venga despues de una noche.",
                }
            )

        holiday_work = person_schedule[(person_schedule["date"].isin(holidays)) & (person_schedule["shift"].isin(["M", "T", "N", "C"]))]
        if not holiday_work.empty:
            alerts.append(
                {
                    "severity": "INFO",
                    "rule": "FESTIVO",
                    "staff": person_schedule.iloc[0]["nombre"],
                    "date": "",
                    "message": f"Trabaja {len(holiday_work)} festivo(s); revisar recargo/compensacion.",
                    "what_to_do": "Verificar que el desprendible incluya el recargo correspondiente.",
                }
            )

    return pd.DataFrame(alerts) if alerts else pd.DataFrame(columns=["severity", "rule", "staff", "date", "message"])
