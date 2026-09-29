"""Crea la base SQLite del MVP a partir del esquema versionado."""

from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

from src.utils.paths import BASE_DIR, DATABASE_PATH

SCHEMA_PATH = BASE_DIR / "database" / "schema.sql"
EXPECTED_SCHEMA_VERSION = 5


def _read_schema_version(database_path: Path) -> int | None:
    if not database_path.exists():
        return None
    with closing(sqlite3.connect(database_path)) as connection:
        table_exists = connection.execute(
            """SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'schema_version'"""
        ).fetchone()
        if not table_exists:
            return None
        row = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return row[0]


def create_database(
    database_path: Path = DATABASE_PATH, *, recreate: bool = False
) -> Path:
    """Crea la base SQLite y evita modificar silenciosamente otro esquema."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    if recreate and database_path.exists():
        database_path.unlink()

    current_version = _read_schema_version(database_path)
    if database_path.exists() and current_version != EXPECTED_SCHEMA_VERSION:
        raise RuntimeError(
            f"La base usa el esquema {current_version}; se esperaba "
            f"{EXPECTED_SCHEMA_VERSION}. Recreala con --recreate."
        )
    if current_version == EXPECTED_SCHEMA_VERSION:
        return database_path

    schema = SCHEMA_PATH.read_text(encoding="utf-8")

    with closing(sqlite3.connect(database_path)) as connection:
        with connection:
            connection.executescript(schema)
            foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
            if foreign_keys != 1:
                raise RuntimeError("SQLite no activó las claves foráneas")
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(
                    f"La base contiene claves foráneas inválidas: {violations}"
                )
            version = connection.execute(
                "SELECT MAX(version) FROM schema_version"
            ).fetchone()[0]
            if version != EXPECTED_SCHEMA_VERSION:
                raise RuntimeError(
                    f"Se creó el esquema {version}; se esperaba "
                    f"{EXPECTED_SCHEMA_VERSION}"
                )

    return database_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DATABASE_PATH,
        help=f"Ruta de salida (por defecto: {DATABASE_PATH})",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Reemplaza la base existente (solo para esquemas sin datos útiles)",
    )
    args = parser.parse_args()
    output = create_database(args.output.resolve(), recreate=args.recreate)
    print(f"Base creada y validada: {output}")


if __name__ == "__main__":
    main()
