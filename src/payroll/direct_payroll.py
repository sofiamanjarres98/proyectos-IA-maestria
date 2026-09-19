from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from src.core.shifts import DEFAULT_SHIFTS, shift_hours
from src.core.time_segments import split_shift


DEVENGO_ORDER = [
    "Salario ordinario",
    "Recargo nocturno",
    "Horas extras diurnas",
    "Horas extras nocturnas",
    "Dominical/festivo ordinario",
]


def _money(value: float) -> float:
    return round(float(value), 2)


def estimate_withholding_source(taxable_base_cop: float, uvt_value: float) -> float:
    """Articulo 383 ET, procedimiento 1. The input base is already depurated."""
    if taxable_base_cop <= 0 or uvt_value <= 0:
        return 0.0
    base_uvt = taxable_base_cop / uvt_value
    if base_uvt <= 95:
        tax_uvt = 0
    elif base_uvt <= 150:
        tax_uvt = (base_uvt - 95) * 0.19
    elif base_uvt <= 360:
        tax_uvt = (base_uvt - 150) * 0.28 + 10
    elif base_uvt <= 640:
        tax_uvt = (base_uvt - 360) * 0.33 + 69
    elif base_uvt <= 945:
        tax_uvt = (base_uvt - 640) * 0.35 + 162
    elif base_uvt <= 2300:
        tax_uvt = (base_uvt - 945) * 0.37 + 268
    else:
        tax_uvt = (base_uvt - 2300) * 0.39 + 770
    return max(tax_uvt * uvt_value, 0.0)


def calculate_direct_payroll(
    schedule: pd.DataFrame,
    staff: pd.DataFrame,
    legal: dict[str, float],
    holidays: set[date],
    novedades: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if schedule.empty:
        return pd.DataFrame(), pd.DataFrame()

    direct_staff = staff[staff["staff_type"] == "DIRECTO"].copy()
    rows = []
    concepts = []
    divisor = float(legal.get("monthly_payroll_hour_divisor", 210) or 210)
    night_surcharge = float(legal.get("night_surcharge", 0.35) or 0.35)
    overtime_day = float(legal.get("overtime_day_surcharge", 0.25) or 0.25)
    overtime_night = float(legal.get("overtime_night_surcharge", 0.75) or 0.75)
    mandatory_rest = float(legal.get("mandatory_rest_surcharge", 0.90) or 0.90)
    employee_health = float(legal.get("employee_health", 0.04) or 0.04)
    employee_pension = float(legal.get("employee_pension", 0.04) or 0.04)
    uvt_value = float(legal.get("uvt_value", 52374) or 52374)
    minimum_wage = float(legal.get("minimum_wage", 1750905) or 1750905)
    exempt_percent = float(legal.get("labor_income_exempt_percent", 0.25) or 0)
    withholding_enabled = bool(int(legal.get("withholding_source_enabled", 1) or 0))

    for person in direct_staff.itertuples(index=False):
        person_schedule = schedule[(schedule["staff_id"] == int(person.id)) & (schedule["shift"].isin(["M", "T", "N", "C"]))].sort_values("date")
        salary = float(person.monthly_salary or 0)
        hourly = salary / divisor if divisor else 0
        worked_hours = float(sum(shift_hours(DEFAULT_SHIFTS[row.shift]) for row in person_schedule.itertuples(index=False)))
        regular_remaining = divisor
        night_regular_hours = 0.0
        extra_day_hours = 0.0
        extra_night_hours = 0.0
        holiday_hours = 0.0

        for assigned in person_schedule.itertuples(index=False):
            for segment in split_shift(assigned.date, DEFAULT_SHIFTS[assigned.shift]):
                hours = float(segment["hours"])
                is_holiday_or_sunday = segment["calendar_date"].weekday() == 6 or segment["calendar_date"] in holidays
                if is_holiday_or_sunday:
                    holiday_hours += hours
                regular_part = min(hours, max(regular_remaining, 0))
                extra_part = max(hours - regular_part, 0)
                if segment["bucket"] == "NOCTURNA":
                    night_regular_hours += regular_part
                    extra_night_hours += extra_part
                else:
                    extra_day_hours += extra_part
                regular_remaining -= regular_part

        recargo_nocturno = night_regular_hours * hourly * night_surcharge
        horas_extra_diurnas = extra_day_hours * hourly * (1 + overtime_day)
        horas_extra_nocturnas = extra_night_hours * hourly * (1 + overtime_night)
        dominical_festivo = holiday_hours * hourly * mandatory_rest
        total_devengado = salary + recargo_nocturno + horas_extra_diurnas + horas_extra_nocturnas + dominical_festivo
        ibc_estimado = salary + recargo_nocturno + horas_extra_diurnas + horas_extra_nocturnas + dominical_festivo
        salud = ibc_estimado * employee_health
        pension = ibc_estimado * employee_pension
        base_after_mandatory = max(ibc_estimado - salud - pension, 0)
        renta_exenta = base_after_mandatory * exempt_percent
        base_retencion = max(base_after_mandatory - renta_exenta, 0)
        retencion_fuente = estimate_withholding_source(base_retencion, uvt_value) if withholding_enabled else 0
        novedades_deduction = 0.0
        person_novedades = pd.DataFrame()
        if novedades is not None and not novedades.empty:
            person_novedades = novedades[novedades["staff_id"] == int(person.id)]
            for novelty in person_novedades.itertuples(index=False):
                days = _overlap_days(novelty.start_date, novelty.end_date, schedule["date"].min(), schedule["date"].max())
                if days <= 0:
                    continue
                daily_salary = salary / 30 if salary else 0
                pay_policy = getattr(novelty, "pay_policy", "NORMAL")
                event_type = getattr(novelty, "event_type", "NOVEDAD")
                pay_percent = float(getattr(novelty, "pay_percent", 1) if pd.notna(getattr(novelty, "pay_percent", 1)) else 1)
                accumulated_start_day = int(
                    getattr(novelty, "accumulated_start_day", 1)
                    if pd.notna(getattr(novelty, "accumulated_start_day", 1))
                    else 1
                )
                if event_type == "INCAPACIDAD_COMUN":
                    amount, detail = _common_incapacity_deduction(salary, days, accumulated_start_day, minimum_wage)
                    novedades_deduction += amount
                    concepts.append(_concept(int(person.id), person.full_name, "DEDUCCION", detail, days, amount))
                    concepts.append(
                        _concept(
                            int(person.id),
                            person.full_name,
                            "NOVEDAD",
                            f"Incapacidad comun desde dia acumulado {accumulated_start_day}",
                            days,
                            0,
                        )
                    )
                elif event_type == "INCAPACIDAD_LABORAL":
                    concepts.append(
                        _concept(
                            int(person.id),
                            person.full_name,
                            "NOVEDAD",
                            "Incapacidad laboral ARL 100%",
                            days,
                            0,
                        )
                    )
                elif pay_policy == "NO_REMUNERADA":
                    amount = daily_salary * days
                    novedades_deduction += amount
                    concepts.append(_concept(int(person.id), person.full_name, "DEDUCCION", event_type, days, amount))
                elif pay_policy == "PORCENTAJE":
                    unpaid_percent = max(1 - pay_percent, 0)
                    amount = daily_salary * days * unpaid_percent
                    novedades_deduction += amount
                    concepts.append(
                        _concept(
                            int(person.id),
                            person.full_name,
                            "DEDUCCION",
                            f"{event_type} ajuste pago {pay_percent:.0%}",
                            days,
                            amount,
                        )
                    )
                elif pay_policy == "PENDIENTE":
                    concepts.append(
                        _concept(
                            int(person.id),
                            person.full_name,
                            "NOVEDAD",
                            f"{event_type} - requiere validacion de nomina",
                            days,
                            0,
                        )
                    )
                else:
                    concepts.append(
                        _concept(
                            int(person.id),
                            person.full_name,
                            "NOVEDAD",
                            f"{event_type} incluida en salario",
                            days,
                            0,
                        )
                    )

        total_deducido = salud + pension + retencion_fuente + novedades_deduction
        neto = total_devengado - total_deducido

        concept_values = {
            "Salario ordinario": (30, salary),
            "Recargo nocturno": (night_regular_hours, recargo_nocturno),
            "Horas extras diurnas": (extra_day_hours, horas_extra_diurnas),
            "Horas extras nocturnas": (extra_night_hours, horas_extra_nocturnas),
            "Dominical/festivo ordinario": (holiday_hours, dominical_festivo),
            "Salud empleado": (30, salud),
            "Pension empleado": (30, pension),
            "Retencion en la fuente": (base_retencion / uvt_value if uvt_value else 0, retencion_fuente),
        }
        for concept, (quantity, amount) in concept_values.items():
            concept_type = "DEDUCCION" if concept in ("Salud empleado", "Pension empleado", "Retencion en la fuente") else "DEVENGO"
            concepts.append(_concept(int(person.id), person.full_name, concept_type, concept, quantity, amount))

        rows.append(
            {
                "staff_id": int(person.id),
                "nombre": person.full_name,
                "salario_basico": _money(salary),
                "horas_trabajadas": _money(worked_hours),
                "horas_nocturnas": _money(night_regular_hours),
                "horas_extra_diurnas": _money(extra_day_hours),
                "horas_extra_nocturnas": _money(extra_night_hours),
                "horas_dominical_festivo": _money(holiday_hours),
                "total_devengado": _money(total_devengado),
                "total_deducido": _money(total_deducido),
                "neto_a_pagar": _money(neto),
                "ibc_estimado": _money(ibc_estimado),
                "base_retencion_estimada": _money(base_retencion),
                "retencion_fuente": _money(retencion_fuente),
                "descuentos_novedades": _money(novedades_deduction),
                "valor_hora_base": _money(hourly),
            }
        )

    return pd.DataFrame(rows), pd.DataFrame(concepts)


def payslip_html(summary: pd.DataFrame, concepts: pd.DataFrame, staff_id: int) -> str:
    person = summary[summary["staff_id"] == staff_id]
    if person.empty:
        return "<p class='muted'>No hay desprendible para este medico.</p>"
    row = person.iloc[0]
    person_concepts = concepts[concepts["staff_id"] == staff_id]
    devengos = person_concepts[person_concepts["tipo"] == "DEVENGO"]
    deducciones = person_concepts[person_concepts["tipo"] == "DEDUCCION"]
    novedades = person_concepts[person_concepts["tipo"] == "NOVEDAD"]

    def money(value: float) -> str:
        return f"${float(value):,.0f}"

    def rows_html(data: pd.DataFrame) -> str:
        html = ""
        for item in data.itertuples(index=False):
            if float(item.valor) == 0 and item.concepto != "Retencion en la fuente":
                continue
            html += (
                "<tr>"
                f"<td>{item.concepto}</td>"
                f"<td>{float(item.cantidad):,.2f}</td>"
                f"<td class='right'>{money(item.valor)}</td>"
                "</tr>"
            )
        return html or "<tr><td colspan='3' class='muted'>Sin conceptos.</td></tr>"

    return f"""
<div class="payslip">
  <div class="payslip-head">
    <div><strong>{row['nombre']}</strong><br><span class="muted">Cargo: Medico general</span></div>
    <div><span class="muted">Basico</span><br><strong>{money(row['salario_basico'])}</strong></div>
    <div><span class="muted">Neto a pagar</span><br><strong class="net">{money(row['neto_a_pagar'])}</strong></div>
  </div>
  <div class="payslip-grid">
    <table class="table">
      <thead><tr><th>Devengos</th><th>Cantidad</th><th>Valor</th></tr></thead>
      <tbody>{rows_html(devengos)}</tbody>
      <tfoot><tr><th colspan="2">Subtotal</th><th class="right">{money(row['total_devengado'])}</th></tr></tfoot>
    </table>
    <table class="table">
      <thead><tr><th>Deducciones</th><th>Cantidad</th><th>Valor</th></tr></thead>
      <tbody>{rows_html(deducciones)}</tbody>
      <tfoot><tr><th colspan="2">Subtotal</th><th class="right">{money(row['total_deducido'])}</th></tr></tfoot>
    </table>
  </div>
  <div class="payslip-novelties">
    <table class="table">
      <thead><tr><th>Novedades registradas</th><th>Dias</th><th>Valor</th></tr></thead>
      <tbody>{rows_html(novedades)}</tbody>
    </table>
  </div>
  <div class="payslip-foot">
    Base IBC estimada: {money(row['ibc_estimado'])}. Base retencion estimada: {money(row['base_retencion_estimada'])}. Valor hora base: {money(row['valor_hora_base'])}.
  </div>
</div>
"""


def _concept(staff_id: int, name: str, concept_type: str, concept: str, quantity: float, amount: float) -> dict:
    return {
        "staff_id": staff_id,
        "nombre": name,
        "tipo": concept_type,
        "concepto": concept,
        "cantidad": _money(quantity),
        "valor": _money(amount),
    }


def _overlap_days(start: date, end: date, period_start: date, period_end: date) -> int:
    start = max(start, period_start)
    end = min(end, period_end)
    if end < start:
        return 0
    return (end - start).days + 1


def _common_incapacity_deduction(salary: float, days: int, accumulated_start_day: int, minimum_wage: float) -> tuple[float, str]:
    if days <= 0:
        return 0.0, "Incapacidad comun"
    daily_salary = salary / 30 if salary else 0
    deduction = 0.0
    has_667 = False
    has_50 = False
    over_180 = False
    for offset in range(days):
        accumulated_day = accumulated_start_day + offset
        if salary <= minimum_wage:
            rate = 1.0
        elif accumulated_day <= 2:
            rate = 1.0
        elif accumulated_day <= 90:
            rate = 2 / 3
            has_667 = True
        elif accumulated_day <= 180:
            rate = 0.5
            has_50 = True
        else:
            rate = 0.5
            over_180 = True
        deduction += daily_salary * (1 - rate)
    parts = ["Incapacidad comun"]
    if has_667:
        parts.append("66.67%")
    if has_50:
        parts.append("50%")
    if over_180:
        parts.append(">180 dias validar AFP/EPS")
    if len(parts) == 1:
        parts.append("100%")
    return deduction, " ".join(parts)
