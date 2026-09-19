from __future__ import annotations

import calendar
import html
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pandas as pd

from src.core.holidays_colombia import colombia_holidays
from src.core.shifts import DEFAULT_SHIFTS, shift_hours
from src.core.time_segments import split_shift
from src.db.database import init_db, load_legal_parameters, load_staff, seed_demo_staff
from src.optimization.scheduler import base_demand, default_availability, demo_novedades, generate_schedule
from src.payroll.direct_payroll import calculate_direct_payroll, payslip_html
from src.payroll.ops_costs import estimate_ops_cost, ops_summary
from src.ui_render import make_summary, render_calendar_html, schedule_pivot
from src.validation import validate_schedule

SHIFT_OPTIONS = ("M", "T", "N", "C", "L", "VAC", "INC", "LIC", "PER", "BLQ")
SCHEDULE_STORE: dict[tuple[int, int, int, int, int], pd.DataFrame] = {}
NOVEDADES_STORE: dict[tuple[int, int], list[dict]] = {}

EVENT_LABELS = {
    "VACACIONES": "Vacaciones",
    "INCAPACIDAD_COMUN": "Incapacidad comun",
    "INCAPACIDAD_LABORAL": "Incapacidad laboral",
    "LICENCIA_REMUNERADA": "Licencia remunerada",
    "LICENCIA_NO_REMUNERADA": "Licencia no remunerada",
    "PERMISO_REMUNERADO": "Permiso remunerado",
    "PERMISO_NO_REMUNERADO": "Permiso no remunerado",
}

NOVELTY_CODES = {
    "VACACIONES": "VAC",
    "INCAPACIDAD_COMUN": "INC",
    "INCAPACIDAD_LABORAL": "INC",
    "LICENCIA_REMUNERADA": "LIC",
    "LICENCIA_NO_REMUNERADA": "LIC",
    "PERMISO_REMUNERADO": "PER",
    "PERMISO_NO_REMUNERADO": "PER",
}


def _table(df: pd.DataFrame) -> str:
    if df.empty:
        return "<p class='muted'>Sin datos.</p>"
    return df.to_html(index=False, classes="table", border=0)


def _alerts_table(alerts: pd.DataFrame) -> str:
    if alerts.empty:
        return "<p class='ok'>No hay problemas pendientes con las reglas actuales.</p>"
    display = alerts.rename(
        columns={
            "severity": "Nivel",
            "rule": "Regla",
            "staff": "Medico",
            "date": "Fecha",
            "message": "Que pasa",
            "what_to_do": "Que hacer",
        }
    )
    wanted = [column for column in ["Nivel", "Medico", "Fecha", "Que pasa", "Que hacer"] if column in display.columns]
    return display[wanted].to_html(index=False, classes="table", border=0)


def _ops_detail_table(ops: pd.DataFrame) -> str:
    if ops.empty:
        return "<p class='muted'>No hay turnos OPS asignados.</p>"
    display = ops.rename(
        columns={
            "nombre": "OPS",
            "fecha": "Fecha",
            "turno": "Turno",
            "horas_diurnas": "Horas diurnas",
            "horas_nocturnas": "Horas nocturnas",
            "costo_diurno": "Pago diurno",
            "costo_nocturno": "Pago nocturno",
            "costo_total": "Total turno",
        }
    )
    wanted = ["OPS", "Fecha", "Turno", "Horas diurnas", "Horas nocturnas", "Pago diurno", "Pago nocturno", "Total turno"]
    return display[wanted].to_html(index=False, classes="table", border=0)


def _default_pay_policy(event_type: str) -> str:
    if event_type in ("LICENCIA_NO_REMUNERADA", "PERMISO_NO_REMUNERADO"):
        return "NO_REMUNERADA"
    if event_type == "INCAPACIDAD_COMUN":
        return "INCAPACIDAD_LEY"
    if event_type == "INCAPACIDAD_LABORAL":
        return "ARL_100"
    return "NORMAL"


def _novedades_for_month(staff: pd.DataFrame, year: int, month: int) -> pd.DataFrame:
    key = (year, month)
    if key not in NOVEDADES_STORE:
        demo = demo_novedades(staff, year, month)
        rows = []
        for idx, row in enumerate(demo.to_dict("records"), start=1):
            rows.append(
                {
                    "id": idx,
                    "staff_id": int(row["staff_id"]),
                    "start_date": row["start_date"],
                    "end_date": row["end_date"],
                    "event_type": row["event_type"],
                    "blocks_assignment": bool(row["blocks_assignment"]),
                    "pay_policy": "NORMAL",
                    "pay_percent": 1.0,
                    "accumulated_start_day": 1,
                    "notes": "Ejemplo editable",
                }
            )
        NOVEDADES_STORE[key] = rows
    return pd.DataFrame(NOVEDADES_STORE[key])


def _novedades_table(novedades: pd.DataFrame, staff: pd.DataFrame, year: int, month: int, m: int, t: int, n: int) -> str:
    if novedades.empty:
        return "<p class='muted'>No hay novedades registradas para este mes.</p>"
    names = staff.set_index("id")["full_name"].to_dict()
    rows = []
    rows.append("<table class='table'><thead><tr><th>Medico</th><th>Tipo</th><th>Desde</th><th>Hasta</th><th>Bloquea turnos</th><th>Pago</th><th>Dia acumulado</th><th>Accion</th></tr></thead><tbody>")
    for item in novedades.itertuples(index=False):
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(names.get(int(item.staff_id), item.staff_id)))}</td>"
            f"<td>{html.escape(EVENT_LABELS.get(item.event_type, item.event_type))}</td>"
            f"<td>{item.start_date}</td>"
            f"<td>{item.end_date}</td>"
            f"<td>{'Si' if item.blocks_assignment else 'No'}</td>"
            f"<td>{html.escape(str(item.pay_policy))}</td>"
            f"<td>{int(item.accumulated_start_day) if pd.notna(item.accumulated_start_day) else ''}</td>"
            "<td>"
            "<form method='post' class='inline-form'>"
            f"<input type='hidden' name='year' value='{year}'>"
            f"<input type='hidden' name='month' value='{month}'>"
            f"<input type='hidden' name='m' value='{m}'>"
            f"<input type='hidden' name='t' value='{t}'>"
            f"<input type='hidden' name='n' value='{n}'>"
            f"<input type='hidden' name='novedad_id' value='{int(item.id)}'>"
            "<button class='danger' type='submit' name='action' value='delete_novedad'>Borrar</button>"
            "</form>"
            "</td>"
            "</tr>"
        )
    rows.append("</tbody></table>")
    return "".join(rows)


def _novedad_form(staff: pd.DataFrame, year: int, month: int, m: int, t: int, n: int) -> str:
    options_staff = "".join(
        f"<option value='{int(row.id)}'>{html.escape(row.full_name)} ({row.staff_type})</option>"
        for row in staff.itertuples(index=False)
    )
    general_events = {
        "VACACIONES": EVENT_LABELS["VACACIONES"],
        "LICENCIA_REMUNERADA": EVENT_LABELS["LICENCIA_REMUNERADA"],
        "LICENCIA_NO_REMUNERADA": EVENT_LABELS["LICENCIA_NO_REMUNERADA"],
        "PERMISO_REMUNERADO": EVENT_LABELS["PERMISO_REMUNERADO"],
        "PERMISO_NO_REMUNERADO": EVENT_LABELS["PERMISO_NO_REMUNERADO"],
    }
    incapacity_events = {
        "INCAPACIDAD_COMUN": EVENT_LABELS["INCAPACIDAD_COMUN"],
        "INCAPACIDAD_LABORAL": EVENT_LABELS["INCAPACIDAD_LABORAL"],
    }
    options_general = "".join(f"<option value='{key}'>{label}</option>" for key, label in general_events.items())
    options_incapacity = "".join(f"<option value='{key}'>{label}</option>" for key, label in incapacity_events.items())
    first_day = date(year, month, 1).isoformat()
    last_day = date(year, month, calendar.monthrange(year, month)[1]).isoformat()
    return f"""
<h3>Vacaciones, licencias y permisos</h3>
<form method="post" class="novelty-form">
  <input type="hidden" name="year" value="{year}">
  <input type="hidden" name="month" value="{month}">
  <input type="hidden" name="m" value="{m}">
  <input type="hidden" name="t" value="{t}">
  <input type="hidden" name="n" value="{n}">
  <label>Medico <select name="staff_id">{options_staff}</select></label>
  <label>Tipo <select name="event_type">{options_general}</select></label>
  <label>Desde <input type="date" name="start_date" value="{first_day}"></label>
  <label>Hasta <input type="date" name="end_date" value="{first_day}" max="{last_day}"></label>
  <label>Bloquea turnos <select name="blocks_assignment"><option value="1">Si</option><option value="0">No</option></select></label>
  <label>Nota <input type="text" name="notes" placeholder="Opcional"></label>
  <button type="submit" name="action" value="add_novedad">Agregar novedad</button>
</form>
<h3 style="margin-top:18px;">Incapacidades</h3>
<div class="explain">
  Aqui si aparece el dia acumulado, porque solo aplica para incapacidad comun continua.
  Ejemplo: si la incapacidad empieza en este mes pero ya venia de antes, escribe el dia acumulado real.
</div>
<form method="post" class="novelty-form">
  <input type="hidden" name="year" value="{year}">
  <input type="hidden" name="month" value="{month}">
  <input type="hidden" name="m" value="{m}">
  <input type="hidden" name="t" value="{t}">
  <input type="hidden" name="n" value="{n}">
  <input type="hidden" name="blocks_assignment" value="1">
  <label>Medico <select name="staff_id">{options_staff}</select></label>
  <label>Tipo <select name="event_type">{options_incapacity}</select></label>
  <label>Desde <input type="date" name="start_date" value="{first_day}"></label>
  <label>Hasta <input type="date" name="end_date" value="{first_day}" max="{last_day}"></label>
  <label>Dia acumulado incapacidad <input type="number" name="accumulated_start_day" min="1" value="1"></label>
  <label>Nota <input type="text" name="notes" placeholder="Opcional"></label>
  <button type="submit" name="action" value="add_novedad">Agregar incapacidad</button>
</form>
"""


def _editable_calendar(schedule: pd.DataFrame, year: int, month: int) -> str:
    if schedule.empty:
        return "<p class='muted'>No hay calendario para editar.</p>"
    pivot = schedule_pivot(schedule)
    day_cols = [col for col in pivot.columns if isinstance(col, int) or str(col).isdigit()]
    rows = []
    rows.append("<table class='edit-cal'><thead><tr><th>Tipo</th><th>Nombre</th>")
    for day in day_cols:
        rows.append(f"<th>{day}</th>")
    rows.append("</tr></thead><tbody>")
    for record in pivot.to_dict("records"):
        staff_id = int(record["staff_id"])
        rows.append(f"<tr><td>{record['tipo']}</td><td class='name'>{record['nombre']}</td>")
        for day in day_cols:
            current = str(record[day] or "L")
            field = f"s_{staff_id}_{int(day)}"
            options = _shift_options(current)
            rows.append(f"<td><select name='{field}' class='shift-{current}'>{options}</select></td>")
        rows.append("</tr>")
    rows.append("</tbody></table>")
    return "".join(rows)


def _calendar_section(schedule: pd.DataFrame, holidays: set, year: int, month: int, staff_type: str) -> str:
    filtered = schedule[schedule["tipo"] == staff_type]
    pivot = schedule_pivot(filtered)
    return render_calendar_html(pivot, holidays, year, month)


def _coverage_panel(schedule: pd.DataFrame, demand: pd.DataFrame, year: int, month: int) -> str:
    assigned = (
        schedule[schedule["shift"].isin(["M", "T", "N", "C"])]
        .groupby(["date", "shift"])
        .size()
        .reset_index(name="asignados")
        if not schedule.empty
        else pd.DataFrame(columns=["date", "shift", "asignados"])
    )
    merged = demand.merge(assigned, on=["date", "shift"], how="left")
    merged["asignados"] = merged["asignados"].fillna(0).astype(int)
    merged["diferencia"] = merged["asignados"] - merged["required"]
    problem_count = int((merged["diferencia"] != 0).sum())
    html = [
        "<div class='coverage-wrap'>",
        f"<div class='coverage-title {'bad' if problem_count else 'good'}'>",
        (
            f"Hay {problem_count} turno(s) con personal faltante o sobrante."
            if problem_count
            else "Cobertura exacta en todos los turnos."
        ),
        "</div>",
        "<table class='coverage'><thead><tr><th>Turno</th>",
    ]
    days = list(range(1, calendar.monthrange(year, month)[1] + 1))
    for day in days:
        html.append(f"<th>{day}</th>")
    html.append("</tr></thead><tbody>")
    for shift in ("M", "T", "N", "C"):
        html.append(f"<tr><td><strong>{shift}</strong></td>")
        for day in days:
            current_date = __import__("datetime").date(year, month, day)
            row = merged[(merged["date"] == current_date) & (merged["shift"] == shift)]
            if row.empty:
                html.append("<td class='okcell'>-</td>")
                continue
            item = row.iloc[0]
            diff = int(item["diferencia"])
            required = int(item["required"])
            assigned_count = int(item["asignados"])
            if required == 0 and assigned_count == 0:
                html.append("<td class='okcell'>-</td>")
            elif diff == 0:
                html.append(f"<td class='okcell'>{assigned_count}/{required}</td>")
            elif diff < 0:
                html.append(f"<td class='badcell'>Falta {abs(diff)}<br><small>{assigned_count}/{required}</small></td>")
            else:
                html.append(f"<td class='badcell'>Sobra {diff}<br><small>{assigned_count}/{required}</small></td>")
        html.append("</tr>")
    html.append("</tbody></table></div>")
    return "".join(html)


def _coverage_gaps(schedule: pd.DataFrame, demand: pd.DataFrame) -> pd.DataFrame:
    assigned = (
        schedule[schedule["shift"].isin(["M", "T", "N", "C"])]
        .groupby(["date", "shift"])
        .size()
        .reset_index(name="asignados")
        if not schedule.empty
        else pd.DataFrame(columns=["date", "shift", "asignados"])
    )
    merged = demand.merge(assigned, on=["date", "shift"], how="left")
    merged["asignados"] = merged["asignados"].fillna(0).astype(int)
    merged["faltan"] = merged["required"] - merged["asignados"]
    return merged[merged["faltan"] > 0].copy()


def _coverage_kpi(schedule: pd.DataFrame, demand: pd.DataFrame) -> tuple[float, int, int]:
    assigned = (
        schedule[schedule["shift"].isin(["M", "T", "N", "C"])]
        .groupby(["date", "shift"])
        .size()
        .reset_index(name="asignados")
        if not schedule.empty
        else pd.DataFrame(columns=["date", "shift", "asignados"])
    )
    merged = demand[demand["required"] > 0].merge(assigned, on=["date", "shift"], how="left")
    merged["asignados"] = merged["asignados"].fillna(0).astype(int)
    total = len(merged)
    complete = int((merged["asignados"] == merged["required"]).sum()) if total else 0
    percent = (complete / total * 100) if total else 100.0
    return percent, complete, total


def _protected_hours_by_staff(novedades: pd.DataFrame, schedule: pd.DataFrame, daily_hours: int = 7) -> dict[int, float]:
    if novedades.empty or schedule.empty:
        return {}
    first_day = schedule["date"].min()
    last_day = schedule["date"].max()
    protected: dict[int, set[date]] = {}
    for row in novedades[novedades["blocks_assignment"] == True].itertuples(index=False):
        staff_id = int(row.staff_id)
        current = max(row.start_date, first_day)
        end = min(row.end_date, last_day)
        while current <= end:
            protected.setdefault(staff_id, set()).add(current)
            current += __import__("datetime").timedelta(days=1)
    return {staff_id: len(days) * daily_hours for staff_id, days in protected.items()}


def _equity_kpi(summary: pd.DataFrame, novedades: pd.DataFrame | None = None, schedule: pd.DataFrame | None = None) -> dict[str, float]:
    direct = summary[summary["tipo"] == "DIRECTO"] if not summary.empty else pd.DataFrame()
    if direct.empty:
        return {"horas_trabajadas": 0, "horas_reconocidas": 0, "noches": 0, "fines": 0, "festivos": 0}
    direct = direct.copy()
    protected = _protected_hours_by_staff(novedades, schedule) if novedades is not None and schedule is not None else {}
    direct["horas_protegidas"] = direct["staff_id"].map(lambda value: protected.get(int(value), 0))
    direct["horas_reconocidas"] = direct["horas"] + direct["horas_protegidas"]
    return {
        "horas_trabajadas": float(direct["horas"].max() - direct["horas"].min()),
        "horas_reconocidas": float(direct["horas_reconocidas"].max() - direct["horas_reconocidas"].min()),
        "noches": float(direct["noches"].max() - direct["noches"].min()),
        "fines": float(direct["fines_semana"].max() - direct["fines_semana"].min()),
        "festivos": float(direct["festivos"].max() - direct["festivos"].min()),
    }


def _is_blocked_by_novelty(staff_id: int, day: date, novedades: pd.DataFrame) -> bool:
    if novedades.empty:
        return False
    matches = novedades[
        (novedades["staff_id"] == staff_id)
        & (novedades["start_date"] <= day)
        & (novedades["end_date"] >= day)
        & (novedades["blocks_assignment"] == True)
    ]
    return not matches.empty


def _coverage_candidate_options(
    schedule: pd.DataFrame,
    staff: pd.DataFrame,
    availability: pd.DataFrame,
    novedades: pd.DataFrame,
    day: date,
    shift: str,
) -> str:
    availability_lookup = {
        (int(row.staff_id), row.date, row.shift): bool(row.available)
        for row in availability.itertuples(index=False)
    }
    month_hours = (
        schedule[schedule["shift"].isin(["M", "T", "N", "C"])]
        .assign(hours=lambda df: df["shift"].map(lambda value: shift_hours(DEFAULT_SHIFTS[value])))
        .groupby("staff_id")["hours"]
        .sum()
        .to_dict()
    )
    options = []
    for person in staff.itertuples(index=False):
        staff_id = int(person.id)
        current = schedule[(schedule["staff_id"] == staff_id) & (schedule["date"] == day)]
        current_shift = current.iloc[0]["shift"] if not current.empty else "L"
        if current_shift in ("M", "T", "N", "C"):
            continue
        if current_shift in ("VAC", "INC", "LIC", "PER", "BLQ"):
            continue
        if _is_blocked_by_novelty(staff_id, day, novedades):
            continue
        if person.staff_type == "OPS" and not availability_lookup.get((staff_id, day, shift), False):
            continue
        previous = schedule[(schedule["staff_id"] == staff_id) & (schedule["date"] == day - __import__("datetime").timedelta(days=1))]
        previous_shift = previous.iloc[0]["shift"] if not previous.empty else "L"
        if previous_shift == "N" and shift in ("M", "T", "C"):
            continue
        projected = float(month_hours.get(staff_id, 0)) + shift_hours(DEFAULT_SHIFTS[shift])
        max_hours = getattr(person, "max_monthly_hours", None)
        if pd.notna(max_hours) and projected > float(max_hours):
            continue
        label = f"{person.full_name} ({person.staff_type}) - {projected:.0f} h si cubre"
        options.append((person.staff_type, projected, person.full_name, staff_id, label))
    options.sort(key=lambda item: (item[0] == "OPS", item[1], item[2]))
    if not options:
        return ""
    return "".join(f"<option value='{staff_id}'>{html.escape(label)}</option>" for _, _, _, staff_id, label in options)


def _coverage_assignment_tool(
    schedule: pd.DataFrame,
    staff: pd.DataFrame,
    demand: pd.DataFrame,
    availability: pd.DataFrame,
    novedades: pd.DataFrame,
    year: int,
    month: int,
    m: int,
    t: int,
    n: int,
) -> str:
    gaps = _coverage_gaps(schedule, demand)
    if gaps.empty:
        return "<p class='ok'>No hay turnos pendientes por cubrir.</p>"
    rows = [
        "<table class='table'><thead><tr><th>Fecha</th><th>Turno</th><th>Faltan</th><th>Candidato sugerido</th><th>Accion</th></tr></thead><tbody>"
    ]
    for item in gaps.itertuples(index=False):
        for missing_idx in range(int(item.faltan)):
            options = _coverage_candidate_options(schedule, staff, availability, novedades, item.date, item.shift)
            if not options:
                candidate_cell = "<span class='muted'>Sin candidatos sin romper reglas</span>"
                action_cell = "<span class='muted'>Revisar disponibilidad o demanda</span>"
            else:
                candidate_cell = f"<select name='cover_staff_id'>{options}</select>"
                action_cell = (
                    "<button type='submit' name='action' value='assign_coverage'>Asignar cobertura</button>"
                )
            rows.append(
                "<tr>"
                f"<td>{item.date}</td>"
                f"<td>{item.shift}</td>"
                f"<td>{int(item.faltan)}</td>"
                "<td>"
                "<form method='post' class='inline-form'>"
                f"<input type='hidden' name='year' value='{year}'>"
                f"<input type='hidden' name='month' value='{month}'>"
                f"<input type='hidden' name='m' value='{m}'>"
                f"<input type='hidden' name='t' value='{t}'>"
                f"<input type='hidden' name='n' value='{n}'>"
                f"<input type='hidden' name='cover_date' value='{item.date}'>"
                f"<input type='hidden' name='cover_shift' value='{item.shift}'>"
                f"{candidate_cell}"
                "</td>"
                f"<td>{action_cell}</form></td>"
                "</tr>"
            )
    rows.append("</tbody></table>")
    return "".join(rows)


def _monthly_extra_hours(schedule: pd.DataFrame, staff: pd.DataFrame, legal: dict[str, float], holidays: set[date]) -> pd.DataFrame:
    if schedule.empty:
        return pd.DataFrame()
    direct_ids = set(staff[staff["staff_type"] == "DIRECTO"]["id"].astype(int).tolist())
    rows = []
    divisor = float(legal.get("monthly_payroll_hour_divisor", 210) or 210)
    running = {staff_id: 0.0 for staff_id in direct_ids}
    for item in schedule.sort_values(["date", "staff_id"]).itertuples(index=False):
        staff_id = int(item.staff_id)
        if staff_id not in direct_ids or item.shift not in ("M", "T", "N", "C"):
            continue
        shift_total = shift_hours(DEFAULT_SHIFTS[item.shift])
        before = running[staff_id]
        regular_remaining = max(divisor - before, 0)
        extra_remaining_after_regular = max(shift_total - regular_remaining, 0)
        extra_day = 0.0
        extra_night = 0.0
        extra_holiday = 0.0
        segment_cursor = 0.0
        for segment in split_shift(item.date, DEFAULT_SHIFTS[item.shift]):
            segment_hours = float(segment["hours"])
            segment_start_in_shift = segment_cursor
            segment_end_in_shift = segment_cursor + segment_hours
            extra_start = max(segment_start_in_shift, regular_remaining)
            extra_hours = max(segment_end_in_shift - extra_start, 0)
            if extra_hours:
                is_rest_day = segment["calendar_date"].weekday() == 6 or segment["calendar_date"] in holidays
                if is_rest_day:
                    extra_holiday += extra_hours
                elif segment["bucket"] == "NOCTURNA":
                    extra_night += extra_hours
                else:
                    extra_day += extra_hours
            segment_cursor = segment_end_in_shift
        extra_hours_total = extra_day + extra_night + extra_holiday
        running[staff_id] += shift_total
        rows.append(
            {
                "staff_id": staff_id,
                "nombre": item.nombre,
                "date": item.date,
                "dia": item.dia,
                "turno": item.shift,
                "horas_turno": shift_total,
                "horas_extra": extra_hours_total,
                "extra_diurna": extra_day,
                "extra_nocturna": extra_night,
                "extra_dominical_festiva": extra_holiday,
            }
        )
    return pd.DataFrame(rows)


def _extra_hours_tool(schedule: pd.DataFrame, staff: pd.DataFrame, legal: dict[str, float], holidays: set[date], year: int, month: int, m: int, t: int, n: int) -> str:
    extras = _monthly_extra_hours(schedule, staff, legal, holidays)
    if extras.empty:
        return "<p class='muted'>No hay datos de horas extra.</p>"
    salary_lookup = staff.set_index("id")["monthly_salary"].to_dict()
    divisor = float(legal.get("monthly_payroll_hour_divisor", 210) or 210)
    overtime_day = float(legal.get("overtime_day_surcharge", 0.25) or 0.25)
    overtime_night = float(legal.get("overtime_night_surcharge", 0.75) or 0.75)
    mandatory_rest = float(legal.get("mandatory_rest_surcharge", 0.90) or 0.90)
    pivot = extras.pivot_table(index=["staff_id", "nombre"], columns="dia", values="horas_extra", aggfunc="sum", fill_value=0).reset_index()
    day_cols = [col for col in pivot.columns if isinstance(col, int) or str(col).isdigit()]
    rows = []
    rows.append("<table class='extra-cal'><thead><tr><th>Medico</th>")
    for day in day_cols:
        rows.append(f"<th>{day}</th>")
    rows.append("<th>Total extra</th><th>Valor estimado a pagar</th></tr></thead><tbody>")
    summary_rows = []
    for record in pivot.to_dict("records"):
        staff_id = int(record["staff_id"])
        total = sum(float(record[day]) for day in day_cols)
        hourly = float(salary_lookup.get(staff_id) or 0) / divisor if divisor else 0
        person_extra = extras[extras["staff_id"] == staff_id]
        extra_day = float(person_extra["extra_diurna"].sum())
        extra_night = float(person_extra["extra_nocturna"].sum())
        extra_holiday = float(person_extra["extra_dominical_festiva"].sum())
        value_day = extra_day * hourly * (1 + overtime_day)
        value_night = extra_night * hourly * (1 + overtime_night)
        value_holiday = extra_holiday * hourly * (1 + mandatory_rest)
        estimated_value = value_day + value_night + value_holiday
        summary_rows.append(
            {
                "medico": record["nombre"],
                "extra_diurna": extra_day,
                "extra_nocturna": extra_night,
                "extra_dominical_festiva": extra_holiday,
                "total_horas_extra_a_pagar": total,
                "valor_extra_diurna": round(value_day, 2),
                "valor_extra_nocturna": round(value_night, 2),
                "valor_extra_dominical_festiva": round(value_holiday, 2),
                "valor_total_extra": round(estimated_value, 2),
            }
        )
        rows.append(f"<tr><td class='name'>{record['nombre']}</td>")
        for day in day_cols:
            value = float(record[day])
            cls = "extra-hot" if value > 0 else "extra-zero"
            text = f"{value:.0f}" if value else ""
            rows.append(f"<td class='{cls}'>{text}</td>")
        rows.append(f"<td><strong>{total:.0f}</strong></td>")
        rows.append(f"<td><strong>${estimated_value:,.0f}</strong></td>")
        rows.append("</tr>")
    rows.append("</tbody></table>")
    return "".join(rows) + "<h3>Resumen horas extra a pagar con recargos</h3>" + _table(pd.DataFrame(summary_rows))


def _editable_calendar_for_type(schedule: pd.DataFrame, staff_type: str, title_cell: str = "Nombre") -> str:
    filtered = schedule[schedule["tipo"] == staff_type]
    if filtered.empty:
        return "<p class='muted'>No hay medicos en este grupo.</p>"
    pivot = schedule_pivot(filtered)
    day_cols = [col for col in pivot.columns if isinstance(col, int) or str(col).isdigit()]
    rows = []
    rows.append(f"<table class='edit-cal'><thead><tr><th>{title_cell}</th>")
    for day in day_cols:
        rows.append(f"<th>{day}</th>")
    rows.append("</tr></thead><tbody>")
    for record in pivot.to_dict("records"):
        staff_id = int(record["staff_id"])
        rows.append(f"<tr><td class='name'>{record['nombre']}</td>")
        for day in day_cols:
            current = str(record[day] or "L")
            field = f"s_{staff_id}_{int(day)}"
            options = _shift_options(current)
            rows.append(f"<td><select name='{field}' class='shift-{current}'>{options}</select></td>")
        rows.append("</tr>")
    rows.append("</tbody></table>")
    return "".join(rows)


def _shift_options(current: str) -> str:
    return "".join(
        f"<option value='{shift}' {'selected' if shift == current else ''}>{shift}</option>"
        for shift in SHIFT_OPTIONS
    )


def _schedule_from_form(
    staff: pd.DataFrame,
    year: int,
    month: int,
    form: dict[str, list[str]],
) -> pd.DataFrame:
    days = calendar.monthrange(year, month)[1]
    rows = []
    for person in staff.itertuples(index=False):
        for day in range(1, days + 1):
            field = f"s_{int(person.id)}_{day}"
            shift = form.get(field, ["L"])[0].upper()
            if shift not in SHIFT_OPTIONS:
                shift = "L"
            rows.append(
                {
                    "staff_id": int(person.id),
                    "nombre": person.full_name,
                    "tipo": person.staff_type,
                    "date": __import__("datetime").date(year, month, day),
                    "dia": day,
                    "shift": shift,
                    "source": "MANUAL",
                }
            )
    return pd.DataFrame(rows)


def _apply_blocking_novelty_to_schedule(schedule: pd.DataFrame, novelty: dict) -> pd.DataFrame:
    if schedule.empty or not novelty.get("blocks_assignment"):
        return schedule
    updated = schedule.copy()
    mask = (
        (updated["staff_id"] == int(novelty["staff_id"]))
        & (updated["date"] >= novelty["start_date"])
        & (updated["date"] <= novelty["end_date"])
    )
    updated.loc[mask, "shift"] = NOVELTY_CODES.get(novelty.get("event_type"), "BLQ")
    updated.loc[mask, "source"] = "NOVEDAD"
    return updated


def _apply_all_blocking_novelties(schedule: pd.DataFrame, novedades: pd.DataFrame) -> pd.DataFrame:
    if schedule.empty or novedades.empty:
        return schedule
    updated = schedule.copy()
    for novelty in novedades.to_dict("records"):
        updated = _apply_blocking_novelty_to_schedule(updated, novelty)
    return updated


def build_page(year: int, month: int, m: int, t: int, n: int, saved: bool = False) -> str:
    init_db()
    seed_demo_staff()
    staff = load_staff()
    legal = load_legal_parameters()
    demand = base_demand(year, month, m, t, n)
    availability = default_availability(staff, year, month)
    novedades = _novedades_for_month(staff, year, month)
    store_key = (year, month, m, t, n)
    if store_key in SCHEDULE_STORE:
        schedule = SCHEDULE_STORE[store_key]
        objective = {"status": "MANUAL", "objective": 0, "message": "Calendario editado manualmente."}
    else:
        schedule, objective = generate_schedule(
            staff,
            demand,
            availability,
            novedades,
            max_shift_hours=legal.get("max_shift_hours", 12),
        )
    schedule = _apply_all_blocking_novelties(schedule, novedades)
    holidays = {item.date for item in colombia_holidays(year)}
    alerts = validate_schedule(schedule, staff, demand, availability, novedades)
    summary = make_summary(schedule, staff, holidays)
    coverage_percent, coverage_complete, coverage_total = _coverage_kpi(schedule, demand)
    equity = _equity_kpi(summary, novedades, schedule)
    ops = estimate_ops_cost(schedule, staff)
    ops_totals = ops_summary(schedule, staff)
    payroll_summary, payroll_concepts = calculate_direct_payroll(schedule, staff, legal, holidays, novedades)
    pivot = schedule_pivot(schedule)
    direct_calendar_html = _calendar_section(schedule, holidays, year, month, "DIRECTO")
    ops_calendar_html = _calendar_section(schedule, holidays, year, month, "OPS")
    coverage_html = _coverage_panel(schedule, demand, year, month)
    direct_editor = _editable_calendar_for_type(schedule, "DIRECTO")
    ops_editor = _editable_calendar_for_type(schedule, "OPS", title_cell="OPS")
    novedades_html = _novedades_table(novedades, staff, year, month, m, t, n)
    novedad_form_html = _novedad_form(staff, year, month, m, t, n)
    coverage_assignment_html = _coverage_assignment_tool(
        schedule,
        staff,
        demand,
        availability,
        novedades,
        year,
        month,
        m,
        t,
        n,
    )
    extra_hours_html = _extra_hours_tool(schedule, staff, legal, holidays, year, month, m, t, n)
    total_ops = ops["costo_total"].sum() if not ops.empty else 0
    total_net = payroll_summary["neto_a_pagar"].sum() if not payroll_summary.empty else 0
    payslips = "".join(
        payslip_html(payroll_summary, payroll_concepts, int(row.staff_id))
        for row in payroll_summary.itertuples(index=False)
    )
    return f"""
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cuadro medico</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 0; color: #111827; background: #f8fafc; }}
    header {{ background: #0f172a; color: white; padding: 18px 28px; }}
    main {{ padding: 22px 28px; }}
    form {{ display: flex; gap: 12px; align-items: end; flex-wrap: wrap; background: white; padding: 14px; border: 1px solid #e5e7eb; }}
    .inline-form {{ display:inline; padding:0; border:0; background:transparent; }}
    .novelty-form {{ align-items:end; }}
    label {{ display: grid; gap: 4px; font-size: 13px; font-weight: 700; }}
    input, select {{ padding: 8px; border: 1px solid #cbd5e1; border-radius: 6px; }}
    button {{ padding: 9px 14px; border: 0; border-radius: 6px; background: #2563eb; color: white; font-weight: 700; }}
    .secondary {{ background:#475569; }}
    .danger {{ background:#b91c1c; padding:6px 10px; }}
    section {{ margin-top: 18px; background: white; padding: 16px; border: 1px solid #e5e7eb; overflow-x: auto; }}
    h1 {{ margin: 0; font-size: 24px; }} h2 {{ margin-top: 0; font-size: 18px; }}
    .cards {{ display:grid; grid-template-columns: repeat(6, minmax(160px, 1fr)); gap:12px; margin-top: 16px; }}
    .card {{ background:white; border:1px solid #e5e7eb; padding:14px; }}
    .metric {{ font-size:22px; font-weight:800; }}
    .muted {{ color:#64748b; }}
    .table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    .table th,.table td {{ border: 1px solid #e5e7eb; padding: 7px; text-align: left; }}
    .table th {{ background: #f1f5f9; }}
    .right {{ text-align:right!important; }}
    .payslip {{ border: 2px solid #111827; margin: 16px 0; background: white; }}
    .payslip-head {{ display:grid; grid-template-columns: 1.5fr 1fr 1fr; gap: 10px; padding: 12px; border-bottom: 2px solid #111827; }}
    .payslip-grid {{ display:grid; grid-template-columns: 1fr 1fr; gap: 0; }}
    .payslip-grid table:first-child {{ border-right: 2px solid #111827; }}
    .payslip-foot {{ padding: 10px 12px; border-top: 2px solid #111827; font-size: 13px; }}
    .payslip-novelties {{ padding: 10px 12px; border-top: 2px solid #111827; }}
    .net {{ font-size: 22px; color: #166534; }}
    .notice {{ background:#ecfdf5; border:1px solid #86efac; padding:10px 12px; margin-top:12px; }}
    .explain {{ background:#eff6ff; border:1px solid #93c5fd; padding:12px; margin: 12px 0; line-height:1.45; }}
    .ok {{ background:#ecfdf5; border:1px solid #86efac; padding:10px; }}
    .edit-cal {{ border-collapse:collapse; width:100%; font-size:12px; }}
    .edit-cal th,.edit-cal td {{ border:1px solid #d1d5db; padding:4px; text-align:center; }}
    .edit-cal th {{ background:#f8fafc; position:sticky; top:0; z-index:1; }}
    .edit-cal select {{ min-width:44px; padding:4px; border-radius:5px; border:1px solid #cbd5e1; font-weight:700; }}
    .shift-M {{ background:#bfdbfe; }} .shift-T {{ background:#fde68a; }} .shift-N {{ background:#c4b5fd; }}
    .shift-C {{ background:#86efac; }} .shift-L {{ background:#e5e7eb; }}
    .shift-VAC {{ background:#bbf7d0; }} .shift-INC {{ background:#fecaca; }} .shift-LIC {{ background:#fed7aa; }}
    .shift-PER {{ background:#bae6fd; }} .shift-BLQ {{ background:#fca5a5; }}
    .coverage-wrap {{ margin-top:14px; }}
    .coverage-title {{ padding:10px; font-weight:800; border:1px solid; margin-bottom:8px; }}
    .coverage-title.bad {{ background:#fee2e2; color:#991b1b; border-color:#fca5a5; }}
    .coverage-title.good {{ background:#dcfce7; color:#166534; border-color:#86efac; }}
    .coverage {{ border-collapse:collapse; width:100%; font-size:12px; }}
    .coverage th,.coverage td {{ border:1px solid #d1d5db; padding:5px; text-align:center; min-width:48px; }}
    .coverage th {{ background:#f8fafc; }}
    .okcell {{ background:#dcfce7; color:#166534; }}
    .badcell {{ background:#dc2626; color:white; font-weight:800; }}
    .badcell small {{ color:white; font-weight:600; }}
    .extra-cal {{ border-collapse:collapse; width:100%; font-size:12px; }}
    .extra-cal th,.extra-cal td {{ border:1px solid #d1d5db; padding:5px; text-align:center; }}
    .extra-cal th {{ background:#f8fafc; }}
    .extra-hot {{ background:#fee2e2; color:#991b1b; font-weight:800; }}
    .extra-zero {{ background:#f8fafc; color:#94a3b8; }}
    @media (max-width: 900px) {{ .payslip-grid, .payslip-head, .cards {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <header>
    <h1>Cuadro mensual de medicos</h1>
    <div class="muted">Vista previa local. La version Streamlit queda en app.py.</div>
  </header>
  <main>
    {"<div class='notice'>Cambios guardados. Validaciones, costos y desprendibles fueron recalculados.</div>" if saved else ""}
    <form method="get">
      <label>Ano <input name="year" type="number" value="{year}" min="2026" max="2035"></label>
      <label>Mes
        <select name="month">
          {''.join(f'<option value="{i}" {"selected" if i == month else ""}>{calendar.month_name[i]}</option>' for i in range(1, 13))}
        </select>
      </label>
      <label>M <input name="m" type="number" value="{m}" min="0" max="10"></label>
      <label>T <input name="t" type="number" value="{t}" min="0" max="10"></label>
      <label>N <input name="n" type="number" value="{n}" min="0" max="10"></label>
      <button>Generar</button>
    </form>
    <div class="cards">
      <div class="card"><div class="muted">Solver</div><div class="metric">{html.escape(str(objective.get("status")))}</div></div>
      <div class="card">
        <div class="muted">Cobertura completa</div>
        <div class="metric">{coverage_percent:.0f}%</div>
        <div class="muted">{coverage_complete}/{coverage_total} turnos</div>
      </div>
      <div class="card">
        <div class="muted">Equidad carga reconocida</div>
        <div class="metric">{equity['horas_reconocidas']:.0f} h</div>
        <div class="muted">Trabajadas {equity['horas_trabajadas']:.0f} h · Noches {equity['noches']:.0f} · FDS {equity['fines']:.0f} · Fest. {equity['festivos']:.0f}</div>
      </div>
      <div class="card"><div class="muted">Alertas</div><div class="metric">{len(alerts)}</div></div>
      <div class="card"><div class="muted">Total OPS</div><div class="metric">${total_ops:,.0f}</div></div>
      <div class="card"><div class="muted">Neto nomina directa</div><div class="metric">${total_net:,.0f}</div></div>
    </div>
    <section>
      <h2>Calendario nomina directa</h2>
      <p class="muted">Este grupo corresponde a medicos de tiempo completo. El sistema busca distribuir horas, noches, fines de semana y festivos con equidad.</p>
      {direct_calendar_html}
      <h2 style="margin-top:18px;">Cobertura por dia y turno</h2>
      <p class="muted">Rojo significa que falta o sobra personal frente a la demanda configurada. Verde significa cobertura exacta.</p>
      {coverage_html}
    </section>
    <section>
      <h2>Editar turnos de nomina directa</h2>
      <p class="muted">Cambia cualquier celda a M, T, N, C o L y guarda. Si dejas un turno descubierto, aparecera en validaciones.</p>
      <form method="post">
        <input type="hidden" name="year" value="{year}">
        <input type="hidden" name="month" value="{month}">
        <input type="hidden" name="m" value="{m}">
        <input type="hidden" name="t" value="{t}">
        <input type="hidden" name="n" value="{n}">
        {direct_editor}
        <h2 style="margin-top:24px;">Calendario OPS separado</h2>
        <p class="muted">
          Los OPS no se equilibran como nomina. Aqui se registran los turnos que aceptan/ofrecen hacer segun su disponibilidad y horas deseadas.
          Puedes marcar M, T, N, C o L para cada contratista y luego guardar para recalcular el pago.
        </p>
        {ops_editor}
        <div style="margin-top:12px; display:flex; gap:10px; flex-wrap:wrap;">
          <button type="submit" name="action" value="save">Guardar cambios y recalcular</button>
          <button class="secondary" type="submit" name="action" value="reset">Regenerar automaticamente</button>
        </div>
      </form>
    </section>
    <section>
      <h2>Novedades de nomina y disponibilidad</h2>
      <div class="explain">
        Usa esta herramienta para vacaciones, incapacidades, licencias y permisos.
        Si la novedad bloquea turnos, el sistema alerta cualquier asignacion dentro de esas fechas.
        Las incapacidades se estiman con reglas legales: comun segun dias acumulados y laboral al 100% por ARL.
        Los dias bloqueados se toman como tiempo protegido para equidad: no se recuperan poniendo mas turnos despues.
        Si falta cobertura, debe cubrirla otro medico y el sistema lo mostrara en rojo.
      </div>
      {novedad_form_html}
      <h3>Novedades activas del mes</h3>
      {novedades_html}
    </section>
    <section>
      <h2>Cubrir turnos pendientes</h2>
      <div class="explain">
        Cuando una incapacidad, vacacion o licencia deja un turno descubierto, aqui aparecen opciones de reemplazo.
        Tu eliges a quien poner; la app recalcula cobertura, horas, extras estimadas y desprendibles.
      </div>
      {coverage_assignment_html}
    </section>
    <section>
      <h2>Calendario de horas extra</h2>
      <div class="explain">
        Esta vista muestra en que dias se generaron horas extra para cada medico de nomina directa.
        Para el MVP se asume que toda hora extra identificada se paga en dinero.
        El valor se calcula segun la franja: extra diurna, extra nocturna o dominical/festiva.
      </div>
      {extra_hours_html}
    </section>
    <section>
      <h2>Vista calendario OPS</h2>
      {ops_calendar_html}
    </section>
    <section><h2>Desprendibles estimados de nomina directa</h2>{payslips}</section>
    <section>
      <h2>Alertas para revisar antes de cerrar</h2>
      <div class="explain">
        Esta tabla no es otro calendario. Es una lista de cosas que el sistema encontro y que una persona debe revisar antes de aprobar el mes:
        turnos descubiertos, exceso frente a la demanda, OPS fuera de disponibilidad, vacaciones, posturnos o festivos.
      </div>
      {_alerts_table(alerts)}
    </section>
    <section><h2>Resumen por medico</h2>{_table(summary)}</section>
    <section>
      <h2>OPS - prestacion de servicios</h2>
      <div class="explain">
        <strong>Como leer OPS:</strong> estos medicos no son nomina directa. Antes del mes informan cuantas horas desean o pueden trabajar.
        El sistema solo deberia asignarlos dentro de su disponibilidad y calcula el pago como
        <strong>horas diurnas * tarifa diurna + horas nocturnas * tarifa nocturna</strong>.
        No se descuentan salud ni pension en este desprendible; si quieres estimar aportes de independientes, debe ir como modulo aparte.
      </div>
      <h3>Resumen OPS</h3>
      {_table(ops_totals)}
      <h3>Pago OPS por cada turno asignado</h3>
      <div class="explain">
        Esta tabla muestra de donde sale el total OPS: cada fila es un turno hecho por un contratista.
        Por ejemplo, una noche N se parte en horas nocturnas y diurnas, y cada parte usa su tarifa.
      </div>
      {_ops_detail_table(ops)}
    </section>
  </main>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        params = parse_qs(urlparse(self.path).query)
        year = int(params.get("year", ["2026"])[0])
        month = int(params.get("month", ["9"])[0])
        m = int(params.get("m", ["2"])[0])
        t = int(params.get("t", ["2"])[0])
        n = int(params.get("n", ["2"])[0])
        saved = params.get("saved", ["0"])[0] == "1"
        body = build_page(year, month, m, t, n, saved=saved).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length).decode("utf-8")
        form = parse_qs(raw)
        year = int(form.get("year", ["2026"])[0])
        month = int(form.get("month", ["9"])[0])
        m = int(form.get("m", ["2"])[0])
        t = int(form.get("t", ["2"])[0])
        n = int(form.get("n", ["2"])[0])
        action = form.get("action", ["save"])[0]
        store_key = (year, month, m, t, n)
        novelty_key = (year, month)
        if action == "reset":
            SCHEDULE_STORE.pop(store_key, None)
        elif action == "add_novedad":
            init_db()
            seed_demo_staff()
            staff = load_staff()
            legal = load_legal_parameters()
            demand = base_demand(year, month, m, t, n)
            availability = default_availability(staff, year, month)
            current_novedades = _novedades_for_month(staff, year, month)
            if store_key not in SCHEDULE_STORE:
                base_schedule, _ = generate_schedule(
                    staff,
                    demand,
                    availability,
                    current_novedades,
                    max_shift_hours=legal.get("max_shift_hours", 12),
                )
                SCHEDULE_STORE[store_key] = _apply_all_blocking_novelties(base_schedule, current_novedades)
            current = list(NOVEDADES_STORE.get(novelty_key, current_novedades.to_dict("records")))
            event_type = form.get("event_type", ["VACACIONES"])[0]
            start = date.fromisoformat(form.get("start_date", [f"{year}-{month:02d}-01"])[0])
            end = date.fromisoformat(form.get("end_date", [start.isoformat()])[0])
            if end < start:
                start, end = end, start
            novelty = {
                "id": (max([int(item["id"]) for item in current], default=0) + 1),
                "staff_id": int(form.get("staff_id", ["0"])[0]),
                "start_date": start,
                "end_date": end,
                "event_type": event_type,
                "blocks_assignment": form.get("blocks_assignment", ["1"])[0] == "1",
                "pay_policy": _default_pay_policy(event_type),
                "pay_percent": 1.0,
                "accumulated_start_day": int(form.get("accumulated_start_day", ["1"])[0] or 1),
                "notes": form.get("notes", [""])[0],
            }
            current.append(novelty)
            NOVEDADES_STORE[novelty_key] = current
            if store_key in SCHEDULE_STORE:
                SCHEDULE_STORE[store_key] = _apply_blocking_novelty_to_schedule(SCHEDULE_STORE[store_key], novelty)
        elif action == "delete_novedad":
            novelty_id = int(form.get("novedad_id", ["0"])[0])
            current = list(NOVEDADES_STORE.get(novelty_key, []))
            NOVEDADES_STORE[novelty_key] = [item for item in current if int(item["id"]) != novelty_id]
        elif action == "assign_coverage":
            init_db()
            seed_demo_staff()
            staff = load_staff()
            legal = load_legal_parameters()
            demand = base_demand(year, month, m, t, n)
            availability = default_availability(staff, year, month)
            novedades = _novedades_for_month(staff, year, month)
            if store_key not in SCHEDULE_STORE:
                base_schedule, _ = generate_schedule(
                    staff,
                    demand,
                    availability,
                    novedades,
                    max_shift_hours=legal.get("max_shift_hours", 12),
                )
                SCHEDULE_STORE[store_key] = _apply_all_blocking_novelties(base_schedule, novedades)
            cover_staff_id = int(form.get("cover_staff_id", ["0"])[0])
            cover_date = date.fromisoformat(form.get("cover_date", [f"{year}-{month:02d}-01"])[0])
            cover_shift = form.get("cover_shift", ["M"])[0]
            schedule = SCHEDULE_STORE[store_key].copy()
            mask = (schedule["staff_id"] == cover_staff_id) & (schedule["date"] == cover_date)
            schedule.loc[mask, "shift"] = cover_shift
            schedule.loc[mask, "source"] = "COBERTURA_MANUAL"
            SCHEDULE_STORE[store_key] = schedule
        else:
            init_db()
            seed_demo_staff()
            staff = load_staff()
            SCHEDULE_STORE[store_key] = _schedule_from_form(staff, year, month, form)
        location = f"/?year={year}&month={month}&m={m}&t={t}&n={n}&saved=1"
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8502), Handler)
    print("Vista previa en http://127.0.0.1:8502")
    server.serve_forever()
