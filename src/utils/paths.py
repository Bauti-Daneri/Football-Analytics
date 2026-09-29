"""Rutas compartidas por el pipeline (Bronze, Silver, Gold)."""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]

DATA_DIR = BASE_DIR / "data"
BRONZE_DIR = DATA_DIR / "bronze"
SILVER_DIR = DATA_DIR / "silver"
MANUAL_EXPORTS_DIR = DATA_DIR / "manual_exports"
MATCH_EXPORTS_DIR = DATA_DIR / "match_exports"
DATABASE_DIR = DATA_DIR / "database"
DATABASE_PATH = DATABASE_DIR / "football_analytics.db"

LOGS_DIR = BASE_DIR / "logs"


def ensure_project_dirs() -> None:
    for base_dir in (BRONZE_DIR, SILVER_DIR, MANUAL_EXPORTS_DIR, MATCH_EXPORTS_DIR, DATABASE_DIR, LOGS_DIR):
        base_dir.mkdir(parents=True, exist_ok=True)
