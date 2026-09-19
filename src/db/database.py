from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.core.legal import INITIAL_LEGAL_PARAMETERS


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "work" / "medical_scheduler.sqlite3"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with connect() as connection:
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        count = connection.execute("SELECT COUNT(*) FROM legal_parameters").fetchone()[0]
        if count == 0:
            connection.executemany(
                """
                INSERT INTO legal_parameters
                (name, value, unit, effective_from, official_source, validated_by, validated_at)
                VALUES (:name, :value, :unit, '2026-09-01', :official_source, 'admin', :validated_at)
                """,
                [{**item, "validated_at": datetime.utcnow().isoformat()} for item in INITIAL_LEGAL_PARAMETERS],
            )
        else:
            existing = {
                row["name"]
                for row in connection.execute("SELECT name FROM legal_parameters").fetchall()
            }
            missing = [item for item in INITIAL_LEGAL_PARAMETERS if item["name"] not in existing]
            if missing:
                connection.executemany(
                    """
                    INSERT INTO legal_parameters
                    (name, value, unit, effective_from, official_source, validated_by, validated_at)
                    VALUES (:name, :value, :unit, '2026-09-01', :official_source, 'admin', :validated_at)
                    """,
                    [{**item, "validated_at": datetime.utcnow().isoformat()} for item in missing],
                )


def seed_demo_staff() -> None:
    direct_names = [
        "Camila Torres",
        "Mateo Ramirez",
        "Isabella Moreno",
        "Daniel Rojas",
        "Valentina Castro",
        "Santiago Herrera",
        "Mariana Duarte",
        "Andres Salazar",
        "Natalia Pardo",
        "Felipe Cardenas",
        "Paula Mejia",
    ]
    ops_names = ["Julian Vargas", "Lucia Bernal"]
    staff = []
    for idx, name in enumerate(direct_names, start=1):
        staff.append(
            {
                "internal_code": f"D{idx:03d}",
                "full_name": name,
                "staff_type": "DIRECTO",
                "monthly_salary": 5970000,
                "ops_day_rate": None,
                "ops_night_rate": None,
                "target_monthly_hours": 210,
                "max_monthly_hours": 240,
                "desired_monthly_hours": None,
            }
        )
    for idx, (name, desired) in enumerate(zip(ops_names, (96, 72)), start=1):
        staff.append(
            {
                "internal_code": f"OPS{idx}",
                "full_name": name,
                "staff_type": "OPS",
                "monthly_salary": None,
                "ops_day_rate": 30000,
                "ops_night_rate": 40000,
                "target_monthly_hours": None,
                "max_monthly_hours": desired + 24,
                "desired_monthly_hours": desired,
            }
        )
    with connect() as connection:
        current = connection.execute("SELECT COUNT(*) FROM staff").fetchone()[0]
        demo_old = connection.execute(
            "SELECT COUNT(*) FROM staff WHERE full_name LIKE 'Medico Directo%' OR full_name LIKE 'Medico OPS%'"
        ).fetchone()[0]
        if current and demo_old == 0:
            return
        if current and demo_old > 0:
            connection.execute("DELETE FROM staff")
        connection.executemany(
            """
            INSERT INTO staff
            (internal_code, full_name, staff_type, monthly_salary, ops_day_rate, ops_night_rate,
             target_monthly_hours, max_monthly_hours, desired_monthly_hours)
            VALUES
            (:internal_code, :full_name, :staff_type, :monthly_salary, :ops_day_rate, :ops_night_rate,
             :target_monthly_hours, :max_monthly_hours, :desired_monthly_hours)
            """,
            staff,
        )


def load_staff() -> pd.DataFrame:
    with connect() as connection:
        return pd.read_sql_query("SELECT * FROM staff WHERE active = 1 ORDER BY staff_type, full_name", connection)


def load_legal_parameters() -> dict[str, float]:
    with connect() as connection:
        rows = connection.execute("SELECT name, value FROM legal_parameters").fetchall()
    return {row["name"]: row["value"] for row in rows}


def audit(entity_type: str, entity_id: str, action: str, before: dict | None, after: dict | None, user_id: str) -> None:
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO audit_log (entity_type, entity_id, action, before_json, after_json, user_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity_type,
                entity_id,
                action,
                json.dumps(before, ensure_ascii=True) if before else None,
                json.dumps(after, ensure_ascii=True) if after else None,
                user_id,
                datetime.utcnow().isoformat(),
            ),
        )
