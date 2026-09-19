from __future__ import annotations

import calendar
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from src.core.holidays_colombia import colombia_holidays
from src.core.shifts import DEFAULT_SHIFTS, shift_hours
from src.db.database import init_db, load_legal_parameters, load_staff, seed_demo_staff
from src.excel.template import build_template, export_schedule
from src.optimization.scheduler import base_demand, default_availability, demo_novedades, generate_schedule
from src.payroll.direct_payroll import calculate_direct_payroll
from src.payroll.ops_costs import estimate_ops_cost, ops_summary
from src.ui_render import make_summary, render_calendar_html, schedule_pivot
from src.validation import validate_schedule


ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT / "outputs"
st.set_page_config(page_title="Cuadro medico", page_icon="🩺", layout="wide")


def ensure_state() -> None:
    init_db()
    seed_demo_staff()
    if "staff" not in st.session_state:
        st.session_state.staff = load_staff()
    if "legal" not in st.session_state:
        st.session_state.legal = load_legal_parameters()


def pivot_to_schedule(edited: pd.DataFrame, original: pd.DataFrame) -> pd.DataFrame:
    day_cols = [col for col in edited.columns if isinstance(col, int) or str(col).isdigit()]
    rows = []
    for record in edited.to_dict("records"):
        for col in day_cols:
            day = int(col)
            rows.append(
                {
                    "staff_id": int(record["staff_id"]),
                    "nombre": record["nombre"],
                    "tipo": record["tipo"],
                    "date": date(original["date"].min().year, original["date"].min().month, day),
                    "dia": day,
                    "shift": str(record[col]).strip().upper() if pd.notna(record[col]) else "L",
                    "source": "MANUAL",
                }
            )
    return pd.DataFrame(rows)
ensure_state()

st.title("Cuadro mensual de medicos")
st.caption("MVP supervisado: genera turnos, valida alertas y deja cada calculo explicable.")

with st.sidebar:
    st.header("Parametros")
    year = st.number_input("Ano", min_value=2026, max_value=2035, value=2026, step=1)
    month = st.selectbox("Mes", list(range(1, 13)), index=8, format_func=lambda m: calendar.month_name[m])
    user_id = st.text_input("Usuario", value="administrador")
    st.divider()
    st.subheader("Demanda base")
    demand_m = st.number_input("M requeridos", min_value=0, max_value=10, value=2)
    demand_t = st.number_input("T requeridos", min_value=0, max_value=10, value=2)
    demand_n = st.number_input("N requeridos", min_value=0, max_value=10, value=2)
    st.divider()
    st.subheader("Prioridades")
    hours_weight = st.slider("Equilibrar horas", 0, 10, 1)
    night_weight = st.slider("Equilibrar noches", 0, 10, 4)
    weekend_weight = st.slider("Equilibrar fines de semana", 0, 10, 2)

staff = st.session_state.staff
legal = st.session_state.legal
demand = base_demand(int(year), int(month), int(demand_m), int(demand_t), int(demand_n))
availability = default_availability(staff, int(year), int(month))
novedades = demo_novedades(staff, int(year), int(month))
holiday_items = colombia_holidays(int(year))
holidays = {item.date for item in holiday_items}

col1, col2, col3, col4 = st.columns(4)
with col1:
    if st.button("Generar turnos", type="primary", use_container_width=True):
        schedule, objective = generate_schedule(
            staff,
            demand,
            availability,
            novedades,
            max_shift_hours=legal.get("max_shift_hours", 12),
            hours_weight=hours_weight,
            night_weight=night_weight,
            weekend_weight=weekend_weight,
        )
        st.session_state.schedule = schedule
        st.session_state.objective = objective
with col2:
    template_path = OUTPUTS / f"plantilla_turnos_{year}_{month:02d}.xlsx"
    build_template(template_path, int(year), int(month))
    with open(template_path, "rb") as file:
        st.download_button("Descargar plantilla", file, file_name=template_path.name, use_container_width=True)
with col3:
    if "schedule" in st.session_state and not st.session_state.schedule.empty:
        summary = make_summary(st.session_state.schedule, staff, holidays)
        alerts = validate_schedule(st.session_state.schedule, staff, demand, availability, novedades)
        export_path = OUTPUTS / f"cuadro_medico_{year}_{month:02d}.xlsx"
        export_schedule(export_path, st.session_state.schedule, summary, alerts)
        with open(export_path, "rb") as file:
            st.download_button("Exportar cuadro", file, file_name=export_path.name, use_container_width=True)
with col4:
    if st.button("Reiniciar demo", use_container_width=True):
        for key in ("schedule", "objective"):
            st.session_state.pop(key, None)
        st.rerun()

tab_calendar, tab_validation, tab_payroll, tab_config = st.tabs(
    ["Calendario", "Validaciones", "OPS y nomina", "Configuracion"]
)

with tab_calendar:
    if "schedule" not in st.session_state:
        st.info("Pulsa 'Generar turnos' para crear el primer calendario.")
    else:
        schedule = st.session_state.schedule
        if schedule.empty:
            st.error(st.session_state.objective.get("message", "No se pudo generar el calendario."))
        else:
            if "objective" in st.session_state:
                st.success(f"Solver: {st.session_state.objective['status']} | Puntaje: {st.session_state.objective['objective']:.0f}")
            pivot = schedule_pivot(schedule)
            st.markdown(render_calendar_html(pivot, holidays, int(year), int(month)), unsafe_allow_html=True)
            st.subheader("Editor manual")
            st.caption("Puedes cambiar M, T, N, C o L. Al aplicar cambios se revalidan cobertura y restricciones.")
            edited = st.data_editor(
                pivot,
                disabled=["staff_id", "tipo", "nombre"],
                use_container_width=True,
                hide_index=True,
                key="calendar_editor",
            )
            if st.button("Aplicar cambios manuales"):
                st.session_state.schedule = pivot_to_schedule(edited, schedule)
                st.success("Cambios aplicados. Revisa la pestana de validaciones.")
                st.rerun()

with tab_validation:
    if "schedule" not in st.session_state or st.session_state.schedule.empty:
        st.info("No hay calendario para validar todavia.")
    else:
        alerts = validate_schedule(st.session_state.schedule, staff, demand, availability, novedades)
        errors = alerts[alerts["severity"] == "ERROR"] if not alerts.empty else pd.DataFrame()
        warnings = alerts[alerts["severity"] == "ADVERTENCIA"] if not alerts.empty else pd.DataFrame()
        st.metric("Errores", len(errors))
        st.metric("Advertencias", len(warnings))
        if alerts.empty:
            st.success("Sin alertas con las reglas actuales.")
        else:
            st.dataframe(alerts, use_container_width=True, hide_index=True)
        st.subheader("Resumen por medico")
        st.dataframe(make_summary(st.session_state.schedule, staff, holidays), use_container_width=True, hide_index=True)

with tab_payroll:
    if "schedule" not in st.session_state or st.session_state.schedule.empty:
        st.info("Genera un calendario para calcular estimaciones.")
    else:
        ops = estimate_ops_cost(st.session_state.schedule, staff)
        st.subheader("Costo OPS estimado")
        st.caption(
            "OPS se calcula como prestacion de servicios: horas asignadas dentro de disponibilidad * tarifa pactada. "
            "No se trata como nomina ni se le descuentan salud/pension en este desprendible."
        )
        st.dataframe(ops_summary(st.session_state.schedule, staff), use_container_width=True, hide_index=True)
        if ops.empty:
            st.info("No hay turnos OPS asignados.")
        else:
            st.metric("Total OPS", f"${ops['costo_total'].sum():,.0f}")
            st.dataframe(ops, use_container_width=True, hide_index=True)
        st.subheader("Referencia nomina directa")
        st.caption("Estimacion separada de nomina. No se mezclan aportes empleador con neto pagado.")
        direct_summary, direct_concepts = calculate_direct_payroll(st.session_state.schedule, staff, legal, holidays)
        st.dataframe(direct_summary, use_container_width=True, hide_index=True)
        st.subheader("Conceptos del desprendible")
        st.dataframe(direct_concepts, use_container_width=True, hide_index=True)

with tab_config:
    st.subheader("Festivos Colombia")
    st.dataframe(pd.DataFrame([holiday.__dict__ for holiday in holiday_items]), use_container_width=True, hide_index=True)
    st.subheader("Parametros legales iniciales")
    st.dataframe(
        pd.DataFrame([{"parametro": key, "valor": value} for key, value in legal.items()]),
        use_container_width=True,
        hide_index=True,
    )
    st.subheader("Personal demo")
    st.dataframe(staff, use_container_width=True, hide_index=True)
