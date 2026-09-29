"""Capa Gold: carga la corrida mas reciente de Silver a la base SQLite
versionada (database/schema.sql, via src/create_database.py).

No inventa datos: cada tabla del esquema relacional se llena solo con lo
que las 9 tablas de Silver realmente traen. Limitacion conocida y
documentada: FBref solo exporta estadisticas de jugador por TEMPORADA
(liga o todas las competencias combinadas), nunca por partido individual,
asi que la tabla `player_match_stats` del esquema queda sin usar hasta
que se incorpore una fuente con detalle partido-a-partido.

`player_season_stats` conserva los scopes que realmente entrega FBref:
la liga con su tabla estandar, cada competencia adicional con el resumen
por competencia y el agregado "Todas las competencias" con las tablas
detalladas combinadas. Las metricas no desglosadas por competencia quedan
en NULL: no se copian totales combinados dentro de Liga o Sudamericana.
La fila de "Todas las competencias" es una agregacion, no una competencia
real — se distingue de las genuinas que vienen de `resultados`."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.create_database import create_database
from src.utils.logging_config import get_logger
from src.utils.paths import DATABASE_PATH, SILVER_DIR, ensure_project_dirs

logger = get_logger("gold_load")

SOURCE_NAME = "fbref-manual"
ALL_COMPETITIONS_NAME = "Todas las competencias"


class GoldLoadError(RuntimeError):
    """Error esperado de carga o validacion tecnica de Gold."""


def stable_id(*values: Any) -> str:
    joined = "|".join(str(v) for v in values)
    return hashlib.md5(joined.encode("utf-8")).hexdigest()[:16]


def _latest_silver_run(entity: str, season: str) -> Path:
    season_dir = SILVER_DIR / entity / season
    if not season_dir.exists():
        raise GoldLoadError(f"No existe Silver para {entity}/{season} en {season_dir}.")
    run_dirs = sorted(
        p for p in season_dir.iterdir() if p.is_dir() and (p / "manifest.json").exists()
    )
    if not run_dirs:
        raise GoldLoadError(f"No hay corridas publicadas de Silver en {season_dir}.")
    return run_dirs[-1]


def _read_silver(silver_dir: Path, logical_name: str) -> pd.DataFrame:
    path = silver_dir / f"{logical_name}.csv"
    if not path.exists():
        raise GoldLoadError(f"Falta {path} en la corrida de Silver.")
    return pd.read_csv(path, encoding="utf-8-sig")


def _na(value: Any) -> Any:
    """Convierte NaN/NA de pandas a None para sqlite3."""
    if pd.isna(value):
        return None
    return value


GOALS_PATTERN = re.compile(r"^\s*(\d+)")

# Filas de resumen que FBref agrega dentro de sus tablas de estadisticas
# de jugador por temporada: no son jugadores reales y no deben crear
# filas en `player`/`player_season_stats`.
NON_PLAYER_ROWS = {"squad total", "opponent total"}


def _parse_goals(value: Any) -> int | None:
    """FBref anota los penales de una tanda como '2 (4)' junto al
    resultado en tiempo reglamentario; solo nos interesa el numero
    principal para la columna `score` (entero) del esquema."""
    value = _na(value)
    if value is None:
        return None
    match = GOALS_PATTERN.match(str(value))
    return int(match.group(1)) if match else None


def _is_real_player(name: Any) -> bool:
    return isinstance(name, str) and name.strip().lower() not in NON_PLAYER_ROWS


def _get_or_create(connection: sqlite3.Connection, table: str, name_col: str, name: str, id_col: str) -> int:
    row = connection.execute(f"SELECT {id_col} FROM {table} WHERE {name_col} = ?", (name,)).fetchone()
    if row:
        return row[0]
    connection.execute(f"INSERT INTO {table}({name_col}) VALUES (?)", (name,))
    return connection.execute(f"SELECT {id_col} FROM {table} WHERE {name_col} = ?", (name,)).fetchone()[0]


def _get_or_create_player(connection: sqlite3.Connection, player_name: str) -> int:
    row = connection.execute(
        "SELECT player_id FROM player_alias WHERE source_name = ? AND source_player_name = ?",
        (SOURCE_NAME, player_name),
    ).fetchone()
    if row:
        return row[0]
    connection.execute("INSERT INTO player(canonical_name) VALUES (?)", (player_name,))
    player_id = connection.execute(
        "SELECT player_id FROM player WHERE canonical_name = ? ORDER BY player_id DESC LIMIT 1", (player_name,)
    ).fetchone()[0]
    connection.execute(
        "INSERT INTO player_alias(source_name, source_player_name, player_id) VALUES (?, ?, ?)",
        (SOURCE_NAME, player_name, player_id),
    )
    return player_id


def _log_data_load(
    connection: sqlite3.Connection, layer: str, source_name: str, source_path: str,
    source_sha256: str | None, row_count: int,
) -> None:
    connection.execute(
        """INSERT INTO data_load(layer, source_name, source_path, source_sha256, status, row_count)
        VALUES (?, ?, ?, ?, 'completed', ?)""",
        (layer, source_name, source_path, source_sha256, row_count),
    )


def _artifact_sha256(silver_dir: Path, logical_name: str) -> str | None:
    manifest_path = silver_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for artifact in manifest.get("artifacts", []):
        if artifact.get("logical_name") == logical_name:
            return artifact.get("sha256")
    return None


def _load_matches(
    connection: sqlite3.Connection, resultados: pd.DataFrame, entity_team_id: int, season_id: int,
) -> tuple[int, int]:
    match_count = 0
    match_team_count = 0
    for _, row in resultados.iterrows():
        date = _na(row.get("Date"))
        if date is None:
            continue
        competition_name = _na(row.get("Comp"))
        opponent_name = _na(row.get("Opponent"))
        if competition_name is None or opponent_name is None:
            continue
        competition_id = _get_or_create(connection, "competition", "name", competition_name, "competition_id")
        opponent_id = _get_or_create(connection, "team", "name", opponent_name, "team_id")

        notes = str(_na(row.get("Notes")) or "").lower()
        if "postponed" in notes or "suspend" in notes:
            result_status = "postponed"
        elif "cancel" in notes:
            result_status = "cancelled"
        elif _na(row.get("Result")) is not None:
            result_status = "played"
        else:
            result_status = "scheduled"

        match_id = stable_id(entity_team_id, date, competition_id, opponent_id)
        captain_name = _na(row.get("Captain"))
        captain_id = (
            _get_or_create_player(connection, captain_name)
            if captain_name and _is_real_player(captain_name)
            else None
        )

        connection.execute(
            """INSERT INTO match(
                match_id, match_date, kickoff_time, season_id, competition_id, round,
                result_status, attendance, referee, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                match_id, date, _na(row.get("Time")), season_id, competition_id, _na(row.get("Round")),
                result_status, _na(row.get("Attendance")), _na(row.get("Referee")), _na(row.get("Notes")),
            ),
        )
        match_count += 1

        is_home = row.get("Venue") == "Home"
        gf, ga = _parse_goals(row.get("GF")), _parse_goals(row.get("GA"))
        poss = _na(row.get("Poss"))

        connection.execute(
            """INSERT INTO match_team(match_id, team_id, venue_role, score, possession, formation, captain_player_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                match_id, entity_team_id, "home" if is_home else "away", gf, poss,
                _na(row.get("Formation")), captain_id,
            ),
        )
        connection.execute(
            """INSERT INTO match_team(match_id, team_id, venue_role, score, possession, formation, captain_player_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                match_id, opponent_id, "away" if is_home else "home", ga,
                (100 - poss) if poss is not None else None, _na(row.get("Opp Formation")), None,
            ),
        )
        match_team_count += 2
    return match_count, match_team_count


PLAYER_SEASON_INT_COLUMNS = {
    "appearances": "MP", "starts": "Starts", "minutes": "Min",
    "goals": "Performance_Gls", "assists": "Performance_Ast",
    "penalties_scored": "PK", "penalties_attempted": "PKatt",
    "yellow_cards": "CrdY", "red_cards": "CrdR",
}
SHOOTING_COLUMNS = {"shots": "Sh", "shots_on_target": "SoT"}
DISCIPLINE_COLUMNS = {
    "fouls_committed": "Fls", "fouls_drawn": "Fld", "offsides": "Off",
    "crosses": "Crs", "interceptions": "Int", "tackles_won": "TklW",
}
GOALKEEPER_COLUMNS = {
    "shots_on_target_against": "SoTA", "goals_against": "GA", "saves": "Saves", "clean_sheets": "CS",
}


def _load_player_season_stats(
    connection: sqlite3.Connection, season_id: int, competition_id: int, entity_team_id: int,
    standard: pd.DataFrame, shooting: pd.DataFrame, discipline: pd.DataFrame, arqueros: pd.DataFrame,
) -> int:
    standard = standard[standard["Player"].map(_is_real_player)]
    merged = standard.merge(
        shooting[[c for c in ("Player", *SHOOTING_COLUMNS.values()) if c in shooting.columns]],
        on="Player", how="left",
    ).merge(
        discipline[[c for c in ("Player", *DISCIPLINE_COLUMNS.values()) if c in discipline.columns]],
        on="Player", how="left",
    ).merge(
        arqueros[[c for c in ("Player", "Age", *GOALKEEPER_COLUMNS.values()) if c in arqueros.columns]],
        on="Player", how="left", suffixes=("", "_gk"),
    )

    count = 0
    for _, row in merged.iterrows():
        player_name = _na(row.get("Player"))
        if player_name is None:
            continue
        player_id = _get_or_create_player(connection, player_name)

        values: dict[str, Any] = {}
        for dest, source in {**PLAYER_SEASON_INT_COLUMNS, **SHOOTING_COLUMNS, **DISCIPLINE_COLUMNS, **GOALKEEPER_COLUMNS}.items():
            values[dest] = _na(row.get(source))

        age_raw = _na(row.get("Age"))
        age_years = None
        if age_raw is not None:
            try:
                age_years = int(str(age_raw).split("-")[0])
            except (ValueError, TypeError):
                age_years = None

        connection.execute(
            """INSERT INTO player_season_stats(
                season_id, competition_id, team_id, player_id, age_years,
                appearances, starts, minutes, goals, assists,
                penalties_scored, penalties_attempted, shots, shots_on_target,
                yellow_cards, red_cards, fouls_committed, fouls_drawn, offsides,
                crosses, tackles_won, interceptions,
                shots_on_target_against, goals_against, saves, clean_sheets
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                season_id, competition_id, entity_team_id, player_id, age_years,
                values["appearances"], values["starts"], values["minutes"], values["goals"], values["assists"],
                values["penalties_scored"], values["penalties_attempted"], values["shots"], values["shots_on_target"],
                values["yellow_cards"], values["red_cards"], values["fouls_committed"], values["fouls_drawn"],
                values["offsides"], values["crosses"], values["tackles_won"], values["interceptions"],
                values["shots_on_target_against"], values["goals_against"], values["saves"], values["clean_sheets"],
            ),
        )
        count += 1
    return count


def _normalise_competition_name(value: str) -> set[str]:
    """Tokens comparables entre encabezados FBref y nombres de resultados."""
    text = value.lower().replace("copa", " ").replace("profesional", " ")
    return set(re.findall(r"[a-záéíóúñ]+", text))


def _competition_prefix_map(
    summary: pd.DataFrame, real_competitions: list[str]
) -> dict[str, str]:
    """Asocia prefijos como `Copa Sudamericana` con `Sudamericana`."""
    prefixes = {
        column[:-3]
        for column in summary.columns
        if column.endswith("_MP") and not column.startswith("Combined_")
    }
    mapping: dict[str, str] = {}
    for prefix in prefixes:
        prefix_tokens = _normalise_competition_name(prefix)
        ranked = sorted(
            (
                (len(prefix_tokens & _normalise_competition_name(name)), name)
                for name in real_competitions
            ),
            reverse=True,
        )
        if ranked and ranked[0][0] > 0:
            mapping[prefix] = ranked[0][1]
    return mapping


def _summary_as_standard(summary: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """Convierte el resumen ancho de una competencia al formato del cargador."""
    renamed = summary.rename(
        columns={
            f"{prefix}_MP": "MP",
            f"{prefix}_Min": "Min",
            f"{prefix}_Gls": "Performance_Gls",
            f"{prefix}_Ast": "Performance_Ast",
        }
    )
    wanted = [
        column
        for column in ("Player", "Age", "MP", "Min", "Performance_Gls", "Performance_Ast")
        if column in renamed.columns
    ]
    result = renamed[wanted].copy()
    # Una fila sin apariciones no representa participacion en esa competencia.
    return result[result["MP"].fillna(0) > 0]


def _summary_goalkeepers(summary: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """Convierte el resumen de arqueros de una competencia."""
    renamed = summary.rename(
        columns={f"{prefix}_GA": "GA", f"{prefix}_CS": "CS"}
    )
    wanted = [column for column in ("Player", "Age", "GA", "CS") if column in renamed.columns]
    return renamed[wanted].copy()


def _run_quality_checks(connection: sqlite3.Connection, load_id: int) -> None:
    fk_status = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    connection.execute(
        """INSERT INTO data_quality_result(load_id, check_name, table_name, severity, passed, observed_value, expected_value)
        VALUES (?, 'foreign_keys_enabled', 'database', 'error', ?, ?, '1')""",
        (load_id, 1 if fk_status == 1 else 0, str(fk_status)),
    )
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    connection.execute(
        """INSERT INTO data_quality_result(load_id, check_name, table_name, severity, passed, observed_value, expected_value)
        VALUES (?, 'no_foreign_key_violations', 'database', 'error', ?, ?, '0')""",
        (load_id, 1 if not violations else 0, str(len(violations))),
    )
    orphan = connection.execute(
        """SELECT COUNT(*) FROM match m
        WHERE (SELECT COUNT(*) FROM match_team mt WHERE mt.match_id = m.match_id) != 2"""
    ).fetchone()[0]
    connection.execute(
        """INSERT INTO data_quality_result(load_id, check_name, table_name, severity, passed, observed_value, expected_value)
        VALUES (?, 'every_match_has_two_teams', 'match', 'error', ?, ?, '0')""",
        (load_id, 1 if orphan == 0 else 0, str(orphan)),
    )


def load_season(entity: str = "river", season: str = "2026", team_name: str = "River Plate") -> Path:
    ensure_project_dirs()
    create_database()
    silver_dir = _latest_silver_run(entity, season)

    resultados = _read_silver(silver_dir, "resultados")
    standard_liga = _read_silver(silver_dir, "stats_jugadores_liga")
    standard_todas = _read_silver(silver_dir, "stats_jugadores_todas_competencias")
    summary_players = _read_silver(silver_dir, "resumen_jugadores_por_competencia")
    summary_goalkeepers = _read_silver(silver_dir, "resumen_arqueros_por_competencia")
    shooting = _read_silver(silver_dir, "tiros")
    discipline = _read_silver(silver_dir, "disciplina")
    arqueros = _read_silver(silver_dir, "arqueros")

    real_competitions = resultados["Comp"].dropna().drop_duplicates().tolist()
    league_name = resultados["Comp"].value_counts().idxmax()
    prefix_map = _competition_prefix_map(summary_players, real_competitions)
    prefix_by_competition = {competition: prefix for prefix, competition in prefix_map.items()}

    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            entity_team_id = _get_or_create(connection, "team", "name", team_name, "team_id")
            season_id = _get_or_create(connection, "season", "label", season, "season_id")
            league_competition_id = _get_or_create(connection, "competition", "name", league_name, "competition_id")
            all_competitions_id = _get_or_create(
                connection, "competition", "name", ALL_COMPETITIONS_NAME, "competition_id"
            )

            # Idempotencia: si esta temporada ya se habia cargado, se
            # descarta y se vuelve a insertar desde la corrida de Silver
            # mas reciente en vez de acumular duplicados.
            connection.execute(
                "DELETE FROM match_team WHERE match_id IN (SELECT match_id FROM match WHERE season_id = ?)",
                (season_id,),
            )
            connection.execute("DELETE FROM match WHERE season_id = ?", (season_id,))
            connection.execute(
                "DELETE FROM player_season_stats WHERE season_id = ? AND team_id = ?",
                (season_id, entity_team_id),
            )

            match_count, match_team_count = _load_matches(connection, resultados, entity_team_id, season_id)
            player_count_liga = _load_player_season_stats(
                connection, season_id, league_competition_id, entity_team_id,
                standard_liga, pd.DataFrame({"Player": []}), pd.DataFrame({"Player": []}),
                _summary_goalkeepers(
                    summary_goalkeepers, prefix_by_competition[league_name]
                ),
            )
            player_counts_real = {league_name: player_count_liga}
            for competition_name in real_competitions:
                if competition_name == league_name:
                    continue
                prefix = prefix_by_competition.get(competition_name)
                if prefix is None:
                    logger.warning("Sin resumen de jugadores para %s", competition_name)
                    continue
                competition_id = _get_or_create(
                    connection, "competition", "name", competition_name, "competition_id"
                )
                player_counts_real[competition_name] = _load_player_season_stats(
                    connection, season_id, competition_id, entity_team_id,
                    _summary_as_standard(summary_players, prefix),
                    pd.DataFrame({"Player": []}), pd.DataFrame({"Player": []}),
                    _summary_goalkeepers(summary_goalkeepers, prefix),
                )
            player_count_todas = _load_player_season_stats(
                connection, season_id, all_competitions_id, entity_team_id,
                standard_todas, shooting, discipline, arqueros,
            )

            for logical_name, df in (
                ("resultados", resultados), ("stats_jugadores_liga", standard_liga),
                ("stats_jugadores_todas_competencias", standard_todas),
                ("resumen_jugadores_por_competencia", summary_players),
                ("resumen_arqueros_por_competencia", summary_goalkeepers),
                ("tiros", shooting), ("disciplina", discipline), ("arqueros", arqueros),
            ):
                _log_data_load(
                    connection, "gold", logical_name,
                    (silver_dir / f"{logical_name}.csv").relative_to(PROJECT_ROOT).as_posix(),
                    _artifact_sha256(silver_dir, logical_name), len(df),
                )
            load_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            _run_quality_checks(connection, load_id)

    logger.info(
        "Gold cargado | temporada=%s | partidos=%s | match_team=%s | jugadores(reales)=%s | jugadores(todas)=%s | %s",
        season, match_count, match_team_count, player_counts_real, player_count_todas, DATABASE_PATH,
    )
    return DATABASE_PATH


def load_all_seasons(entity: str = "river", team_name: str = "River Plate") -> dict[str, Path]:
    ensure_project_dirs()
    entity_dir = SILVER_DIR / entity
    if not entity_dir.exists():
        raise GoldLoadError(f"No hay datos de Silver para la entidad '{entity}'.")
    seasons = sorted(p.name for p in entity_dir.iterdir() if p.is_dir())
    if not seasons:
        raise GoldLoadError(f"No hay temporadas publicadas en Silver para '{entity}'.")
    return {season: load_season(entity, season, team_name) for season in seasons}


def main() -> None:
    parser = argparse.ArgumentParser(description="Carga Gold: Silver -> base SQLite.")
    parser.add_argument("--entity", default="river")
    parser.add_argument("--season", help="Si se omite, carga todas las temporadas disponibles en Silver.")
    parser.add_argument("--team-name", default="River Plate")
    args = parser.parse_args()
    if args.season:
        load_season(args.entity, args.season, args.team_name)
    else:
        published = load_all_seasons(args.entity, args.team_name)
        for season in published:
            logger.info("Temporada %s cargada en %s", season, DATABASE_PATH)


if __name__ == "__main__":
    main()
