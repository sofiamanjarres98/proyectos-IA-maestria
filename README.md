# Programacion medica

MVP en Streamlit para generar, revisar y auditar cuadros mensuales de turnos medicos en Colombia.

## Ejecutar

```powershell
streamlit run app.py
```

La aplicacion crea datos anonimizados de ejemplo, genera una plantilla Excel y permite exportar el calendario resultante.

## Alcance actual

- Personal directo y OPS.
- Festivos Colombia calculados por ano.
- Turnos M, T, N, C y L con maximo duro de 12 horas.
- Optimizacion CP-SAT con OR-Tools.
- Validaciones de cobertura, disponibilidad OPS, vacaciones/restricciones, descanso posturno y maximo de horas.
- Estimacion OPS por franjas diurna/nocturna.
- Auditoria inicial en SQLite preparada para extender.
