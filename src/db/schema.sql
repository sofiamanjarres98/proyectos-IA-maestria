CREATE TABLE IF NOT EXISTS staff (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  internal_code TEXT,
  full_name TEXT NOT NULL,
  staff_type TEXT NOT NULL CHECK (staff_type IN ('DIRECTO', 'OPS')),
  active INTEGER NOT NULL DEFAULT 1,
  monthly_salary REAL,
  ops_day_rate REAL DEFAULT 30000,
  ops_night_rate REAL DEFAULT 40000,
  target_monthly_hours REAL,
  max_monthly_hours REAL,
  desired_monthly_hours REAL
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  action TEXT NOT NULL,
  before_json TEXT,
  after_json TEXT,
  user_id TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS legal_parameters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  value REAL NOT NULL,
  unit TEXT NOT NULL,
  effective_from TEXT,
  effective_to TEXT,
  official_source TEXT,
  validated_by TEXT,
  validated_at TEXT
);
