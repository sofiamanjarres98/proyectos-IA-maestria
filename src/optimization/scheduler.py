from __future__ import annotations

import calendar
from datetime import date, timedelta

import pandas as pd

try:
    from ortools.sat.python import cp_model
except ModuleNotFoundError:  # pragma: no cover - used only for dependency-limited preview.
    cp_model = None

from src.core.holidays_colombia import colombia_holidays
from src.core.shifts import DEFAULT_SHIFTS, shift_hours


WORK_SHIFTS = ["M", "T", "N", "C"]


def month_dates(year: int, month: int) -> list[date]:
    days = calendar.monthrange(year, month)[1]
    return [date(year, month, day) for day in range(1, days + 1)]


def base_demand(year: int, month: int, m: int = 2, t: int = 2, n: int = 2) -> pd.DataFrame:
    rows = []
    for day in month_dates(year, month):
        rows.extend(
            [
                {"date": day, "shift": "M", "required": m},
                {"date": day, "shift": "T", "required": t},
                {"date": day, "shift": "N", "required": n},
                {"date": day, "shift": "C", "required": 0},
            ]
        )
    return pd.DataFrame(rows)


def default_availability(staff: pd.DataFrame, year: int, month: int) -> pd.DataFrame:
    rows = []
    for _, person in staff.iterrows():
        for day in month_dates(year, month):
            for shift in WORK_SHIFTS:
                available = True
                if person["staff_type"] == "OPS":
                    # Demo: OPS offers alternate dates to make the behavior visible.
                    available = (int(person["id"]) + day.day) % 2 == 0 or shift == "N"
                rows.append({"staff_id": int(person["id"]), "date": day, "shift": shift, "available": available})
    return pd.DataFrame(rows)


def demo_novedades(staff: pd.DataFrame, year: int, month: int) -> pd.DataFrame:
    rows = []
    direct = staff[staff["staff_type"] == "DIRECTO"]
    if not direct.empty:
        staff_id = int(direct.iloc[0]["id"])
        start_day = date(year, month, min(3, calendar.monthrange(year, month)[1]))
        end_day = min(start_day + timedelta(days=2), date(year, month, calendar.monthrange(year, month)[1]))
        rows.append(
            {
                "staff_id": staff_id,
                "start_date": start_day,
                "end_date": end_day,
                "event_type": "VACACIONES",
                "blocks_assignment": True,
            }
        )
    return pd.DataFrame(rows)


def _blocked(staff_id: int, day: date, novedades: pd.DataFrame) -> bool:
    if novedades.empty:
        return False
    matches = novedades[
        (novedades["staff_id"] == staff_id)
        & (novedades["start_date"] <= day)
        & (novedades["end_date"] >= day)
        & (novedades["blocks_assignment"] == True)
    ]
    return not matches.empty


def _protected_absence_hours(staff_id: int, dates: list[date], novedades: pd.DataFrame, daily_hours: int = 7) -> int:
    if novedades.empty:
        return 0
    protected_days = set()
    first_day = min(dates)
    last_day = max(dates)
    matches = novedades[
        (novedades["staff_id"] == staff_id)
        & (novedades["blocks_assignment"] == True)
        & (novedades["end_date"] >= first_day)
        & (novedades["start_date"] <= last_day)
    ]
    for row in matches.itertuples(index=False):
        start = max(row.start_date, first_day)
        end = min(row.end_date, last_day)
        current = start
        while current <= end:
            protected_days.add(current)
            current += timedelta(days=1)
    return len(protected_days) * daily_hours


def generate_schedule(
    staff: pd.DataFrame,
    demand: pd.DataFrame,
    availability: pd.DataFrame,
    novedades: pd.DataFrame,
    *,
    max_shift_hours: float = 12,
    weekend_weight: int = 2,
    night_weight: int = 4,
    hours_weight: int = 1,
) -> tuple[pd.DataFrame, dict]:
    if cp_model is None:
        return _generate_greedy_preview(staff, demand, availability, novedades, max_shift_hours=max_shift_hours)

    model = cp_model.CpModel()
    staff_ids = [int(value) for value in staff["id"].tolist()]
    dates = sorted(demand["date"].unique())
    holidays = {holiday.date for holiday in colombia_holidays(dates[0].year)}
    staff_lookup = staff.set_index("id").to_dict("index")
    protected_hours = {
        staff_id: _protected_absence_hours(staff_id, dates, novedades)
        if staff_lookup[staff_id]["staff_type"] == "DIRECTO"
        else 0
        for staff_id in staff_ids
    }
    availability_lookup = {
        (int(row.staff_id), row.date, row.shift): bool(row.available)
        for row in availability.itertuples(index=False)
    }

    x = {}
    for staff_id in staff_ids:
        for day in dates:
            for shift in WORK_SHIFTS:
                if shift_hours(DEFAULT_SHIFTS[shift]) > max_shift_hours:
                    continue
                allowed = availability_lookup.get((staff_id, day, shift), True)
                if _blocked(staff_id, day, novedades):
                    allowed = False
                var = model.NewBoolVar(f"x_{staff_id}_{day}_{shift}")
                x[(staff_id, day, shift)] = var
                if not allowed:
                    model.Add(var == 0)

    for day in dates:
        for shift in WORK_SHIFTS:
            required = int(demand[(demand["date"] == day) & (demand["shift"] == shift)]["required"].iloc[0])
            vars_for_slot = [x[(staff_id, day, shift)] for staff_id in staff_ids if (staff_id, day, shift) in x]
            model.Add(sum(vars_for_slot) == required)

    for staff_id in staff_ids:
        for day in dates:
            model.Add(sum(x[(staff_id, day, shift)] for shift in WORK_SHIFTS if (staff_id, day, shift) in x) <= 1)

    for staff_id in staff_ids:
        for day in dates[:-1]:
            next_day = day + timedelta(days=1)
            if (staff_id, day, "N") in x:
                for next_shift in ("M", "T", "C"):
                    if (staff_id, next_day, next_shift) in x:
                        model.Add(x[(staff_id, day, "N")] + x[(staff_id, next_day, next_shift)] <= 1)

    total_hours = {}
    night_counts = {}
    weekend_counts = {}
    holiday_counts = {}
    for staff_id in staff_ids:
        total_hours[staff_id] = model.NewIntVar(0, 400, f"hours_{staff_id}")
        night_counts[staff_id] = model.NewIntVar(0, 31, f"nights_{staff_id}")
        weekend_counts[staff_id] = model.NewIntVar(0, 31, f"weekends_{staff_id}")
        holiday_counts[staff_id] = model.NewIntVar(0, 31, f"holidays_{staff_id}")
        model.Add(
            total_hours[staff_id]
            == sum(
                int(shift_hours(DEFAULT_SHIFTS[shift])) * x[(staff_id, day, shift)]
                for day in dates
                for shift in WORK_SHIFTS
                if (staff_id, day, shift) in x
            )
        )
        model.Add(night_counts[staff_id] == sum(x[(staff_id, day, "N")] for day in dates if (staff_id, day, "N") in x))
        model.Add(
            weekend_counts[staff_id]
            == sum(
                x[(staff_id, day, shift)]
                for day in dates
                for shift in WORK_SHIFTS
                if (staff_id, day, shift) in x and day.weekday() >= 5
            )
        )
        model.Add(
            holiday_counts[staff_id]
            == sum(
                x[(staff_id, day, shift)]
                for day in dates
                for shift in WORK_SHIFTS
                if (staff_id, day, shift) in x and day in holidays
            )
        )
        max_hours = staff_lookup[staff_id].get("max_monthly_hours")
        if pd.notna(max_hours):
            model.Add(total_hours[staff_id] <= int(max_hours))

    objective_terms = []
    for staff_id in staff_ids:
        target = staff_lookup[staff_id].get("desired_monthly_hours")
        if staff_lookup[staff_id]["staff_type"] == "DIRECTO":
            target = staff_lookup[staff_id].get("target_monthly_hours")
        if pd.isna(target):
            target = 180
        diff = model.NewIntVar(0, 400, f"target_diff_{staff_id}")
        model.AddAbsEquality(diff, total_hours[staff_id] + protected_hours[staff_id] - int(target))
        objective_terms.append(hours_weight * diff)

    for group_name in ("DIRECTO", "OPS"):
        group_ids = [int(row.id) for row in staff[staff["staff_type"] == group_name].itertuples()]
        for i, left in enumerate(group_ids):
            for right in group_ids[i + 1 :]:
                n_diff = model.NewIntVar(0, 31, f"night_diff_{left}_{right}")
                w_diff = model.NewIntVar(0, 31, f"weekend_diff_{left}_{right}")
                model.AddAbsEquality(n_diff, night_counts[left] - night_counts[right])
                model.AddAbsEquality(w_diff, weekend_counts[left] - weekend_counts[right])
                objective_terms.append(night_weight * n_diff)
                objective_terms.append(weekend_weight * w_diff)

    model.Minimize(sum(objective_terms))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 15
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return pd.DataFrame(), {
            "status": solver.StatusName(status),
            "message": "No existe una solucion factible con cobertura exacta. Revise disponibilidad, novedades, maximos de horas y demanda.",
        }

    rows = []
    for staff_id in staff_ids:
        person = staff_lookup[staff_id]
        for day in dates:
            assigned = "L"
            for shift in WORK_SHIFTS:
                if (staff_id, day, shift) in x and solver.Value(x[(staff_id, day, shift)]):
                    assigned = shift
                    break
            rows.append(
                {
                    "staff_id": staff_id,
                    "nombre": person["full_name"],
                    "tipo": person["staff_type"],
                    "date": day,
                    "dia": day.day,
                    "shift": assigned,
                    "source": "OPTIMIZER",
                }
            )
    objective = {
        "status": solver.StatusName(status),
        "objective": solver.ObjectiveValue(),
        "hours_weight": hours_weight,
        "night_weight": night_weight,
        "weekend_weight": weekend_weight,
    }
    return pd.DataFrame(rows), objective


def _generate_greedy_preview(
    staff: pd.DataFrame,
    demand: pd.DataFrame,
    availability: pd.DataFrame,
    novedades: pd.DataFrame,
    *,
    max_shift_hours: float,
) -> tuple[pd.DataFrame, dict]:
    dates = sorted(demand["date"].unique())
    staff_ids = [int(value) for value in staff["id"].tolist()]
    staff_lookup = staff.set_index("id").to_dict("index")
    protected_hours = {
        staff_id: _protected_absence_hours(staff_id, dates, novedades)
        if staff_lookup[staff_id]["staff_type"] == "DIRECTO"
        else 0
        for staff_id in staff_ids
    }
    availability_lookup = {
        (int(row.staff_id), row.date, row.shift): bool(row.available)
        for row in availability.itertuples(index=False)
    }
    assigned: dict[tuple[int, date], str] = {}
    totals = {staff_id: 0.0 for staff_id in staff_ids}
    nights = {staff_id: 0 for staff_id in staff_ids}
    weekends = {staff_id: 0 for staff_id in staff_ids}

    for day in dates:
        for shift in WORK_SHIFTS:
            required = int(demand[(demand["date"] == day) & (demand["shift"] == shift)]["required"].iloc[0])
            if shift_hours(DEFAULT_SHIFTS[shift]) > max_shift_hours:
                continue
            for _ in range(required):
                candidates = []
                for staff_id in staff_ids:
                    if (staff_id, day) in assigned:
                        continue
                    if not availability_lookup.get((staff_id, day, shift), True):
                        continue
                    if _blocked(staff_id, day, novedades):
                        continue
                    previous_shift = assigned.get((staff_id, day - timedelta(days=1)))
                    if previous_shift == "N" and shift in ("M", "T", "C"):
                        continue
                    max_hours = staff_lookup[staff_id].get("max_monthly_hours")
                    next_hours = totals[staff_id] + shift_hours(DEFAULT_SHIFTS[shift])
                    if pd.notna(max_hours) and next_hours > float(max_hours):
                        continue
                    type_bias = 0 if staff_lookup[staff_id]["staff_type"] == "DIRECTO" else 8
                    candidates.append(
                        (
                            totals[staff_id] + protected_hours[staff_id],
                            nights[staff_id] if shift == "N" else 0,
                            weekends[staff_id] if day.weekday() >= 5 else 0,
                            type_bias,
                            staff_id,
                        )
                    )
                if candidates:
                    _, _, _, _, chosen = min(candidates)
                    assigned[(chosen, day)] = shift
                    totals[chosen] += shift_hours(DEFAULT_SHIFTS[shift])
                    if shift == "N":
                        nights[chosen] += 1
                    if day.weekday() >= 5:
                        weekends[chosen] += 1

    rows = []
    for staff_id in staff_ids:
        person = staff_lookup[staff_id]
        for day in dates:
            rows.append(
                {
                    "staff_id": staff_id,
                    "nombre": person["full_name"],
                    "tipo": person["staff_type"],
                    "date": day,
                    "dia": day.day,
                    "shift": assigned.get((staff_id, day), "L"),
                    "source": "GREEDY_PREVIEW",
                }
            )
    return pd.DataFrame(rows), {
        "status": "PREVIEW_GREEDY",
        "objective": 0,
        "message": "Vista previa sin OR-Tools. Instalar OR-Tools activa optimizacion CP-SAT.",
    }
