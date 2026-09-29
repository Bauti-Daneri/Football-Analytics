"""Carga el detalle jugador-a-jugador de UN partido puntual (el "Match
Report" de FBref) a `player_match_stats`, y completa el resultado del
partido en `match`/`match_team` si todavia figuraba como 'scheduled'.

A diferencia de `gold_load.py` (que carga estadisticas agregadas por
TEMPORADA), FBref no ofrece esto para toda la temporada de una — hay que
exportar manualmente, partido por partido, las tablas de la pagina
"Match Report" de ese encuentro (Share & Export -> Get as Excel Workbook,
una vez por tabla, igual que las 9 tablas de temporada). Por eso este
loader se corre UNA VEZ POR PARTIDO que se quiera sumar, no reemplaza a
`gold_load.py` sino que lo complementa.

Que tablas exportar de la pagina del partido (fbref.com/en/matches/...):
para cada uno de los dos equipos, la tabla "Player Stats -> Summary"
(estadisticas de campo, columnas con el grupo "Performance") y la tabla
"Goalkeeper Stats" (columnas con el grupo "Shot Stopping"). Osea 4
archivos por partido (2 por equipo). No hace falta decir cual archivo es
de que equipo: se identifica solo comparando los nombres de jugadores
de cada tabla contra los jugadores ya conocidos del equipo local
(`--entity`) via `player_season_stats`; la tabla que no coincide queda
para el rival.

Importante: estos exports van en `data/match_exports/`, NUNCA dentro de
`data/manual_exports/` (donde van las 9 tablas de temporada). Los dos
carpetas parecen intercambiables pero no lo son: `bronze_ingest.py`
recorre TODO `data/manual_exports/` recursivamente, y un archivo de
Match Report ahi adentro puede terminar mal identificado como una tabla
de temporada por pura coincidencia de columnas (p. ej. la tabla
"Summary" de un partido comparte columnas con "disciplina": CrdY, Fls,
Off, Crs) -- corrompería la carga de temporada en vez de solo fallar
con un error. `data/match_exports/` es una carpeta hermana, fuera de
ese recorrido, para evitar justamente eso.

Limitacion conocida: como Silver no versiona todavia estos exports (no
hay una carpeta `data/silver/.../match_reports/`), este loader lee
directo de `data/match_exports/`, sin pasar por Bronze/Silver. Si en
algun momento se cargan muchos partidos regularmente, conviene sumarle
su propia carpeta versionada como las demas capas.
"""

from __future__ import annotations

import argparse
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

from src.gold_load import GoldLoadError, _get_or_create_player, _na  # noqa: E402
from src.utils.logging_config import get_logger  # noqa: E402
from src.utils.paths import DATABASE_PATH, MATCH_EXPORTS_DIR  # noqa: E402

logger = get_logger("load_match_report")

TOTAL_ROW_PATTERN = re.compile(r"^\d+\s+Players?$", re.IGNORECASE)

FIELD_COLUMNS = {
    "goals": "Performance_Gls", "assists": "Performance_Ast",
    "penalties_scored": "Performance_PK", "penalties_attempted": "Performance_PKatt",
    "shots": "Performance_Sh", "shots_on_target": "Performance_SoT",
    "yellow_cards": "Performance_CrdY", "red_cards": "Performance_CrdR",
    "fouls_committed": "Performance_Fls", "fouls_drawn": "Performance_Fld",
    "offsides": "Performance_Off", "crosses": "Performance_Crs",
    "tackles_won": "Performance_TklW", "interceptions": "Performance_Int",
    "own_goals": "Performance_OG", "penalties_won": "Performance_PKwon",
    "penalties_conceded": "Performance_PKcon",
}
KEEPER_COLUMNS = {
    "shots_on_target_against": "Shot Stopping_SoTA",
    "goals_against": "Shot Stopping_GA",
    "saves": "Shot Stopping_Saves",
}


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    flat = []
    for col in df.columns:
        parts = [str(p) for p in col if not str(p).startswith("Unnamed")]
        flat.append("_".join(parts) if parts else str(col[-1]))
    df = df.copy()
    df.columns = flat
    return df


def _is_real_row(name: Any) -> bool:
    if not isinstance(name, str):
        return False
    name = name.strip()
    if not name or TOTAL_ROW_PATTERN.match(name):
        return False
    return name.lower() not in {"squad total", "opponent total"}


def _read_export_tables(export_dir: Path) -> list[pd.DataFrame]:
    tables = []
    for path in sorted(export_dir.glob("*.xls")):
        try:
            raw = pd.read_html(path, encoding="utf-8")[0]
        except ValueError:
            continue
        tables.append(_flatten_columns(raw))
    return tables


def _dedup_by_roster(tables: list[pd.DataFrame]) -> list[pd.DataFrame]:
    seen: list[tuple[str, ...]] = []
    unique: list[pd.DataFrame] = []
    for df in tables:
        key = tuple(df["Player"].astype(str).tolist())
        if key in seen:
            continue
        seen.append(key)
        unique.append(df)
    return unique


def _known_roster(connection: sqlite3.Connection, team_id: int) -> set[str]:
    rows = connection.execute(
        "SELECT DISTINCT p.canonical_name FROM player p "
        "JOIN player_season_stats pss ON pss.player_id = p.player_id "
        "WHERE pss.team_id = ?",
        (team_id,),
    ).fetchall()
    return {row[0] for row in rows}


def _resolve_match(
    connection: sqlite3.Connection, entity_team_id: int, opponent_name: str, match_date: str,
) -> tuple[str, int]:
    row = connection.execute(
        """SELECT mt_entity.match_id, mt_opp.team_id
        FROM match m
        JOIN match_team mt_entity ON mt_entity.match_id = m.match_id AND mt_entity.team_id = ?
        JOIN match_team mt_opp ON mt_opp.match_id = m.match_id AND mt_opp.team_id != ?
        JOIN team t_opp ON t_opp.team_id = mt_opp.team_id
        WHERE m.match_date = ? AND t_opp.name = ?""",
        (entity_team_id, entity_team_id, match_date, opponent_name),
    ).fetchone()
    if row is None:
        raise GoldLoadError(
            f"No encontre en `match` un partido de team_id={entity_team_id} vs '{opponent_name}' "
            f"con fecha {match_date}. Tiene que existir antes (cargado por gold_load.py desde "
            f"`resultados`) para poder asociarle el detalle por jugador."
        )
    return row[0], row[1]


def load_match_report(
    connection: sqlite3.Connection, match_id: str, entity_team_id: int, opponent_team_id: int,
    export_dir: Path,
) -> dict[str, int]:
    tables = _dedup_by_roster(_read_export_tables(export_dir))
    summary_tables = [df for df in tables if "Performance_Gls" in df.columns]
    keeper_tables = [df for df in tables if "Shot Stopping_SoTA" in df.columns]

    if len(summary_tables) != 2:
        raise GoldLoadError(
            f"Se esperaban 2 tablas de resumen de campo (una por equipo) en {export_dir}, "
            f"se encontraron {len(summary_tables)}. Revisa que hayas exportado la tabla "
            f"'Summary' de cada equipo desde el Match Report de FBref."
        )

    known_entity = _known_roster(connection, entity_team_id)
    summary_tables.sort(
        key=lambda df: len(set(df["Player"]) & known_entity), reverse=True,
    )
    team_summary = {entity_team_id: summary_tables[0], opponent_team_id: summary_tables[1]}

    team_keeper: dict[int, pd.DataFrame | None] = {}
    for team_id, summary_df in team_summary.items():
        roster = {n for n in summary_df["Player"] if _is_real_row(n)}
        team_keeper[team_id] = next(
            (df for df in keeper_tables if set(df["Player"]) & roster), None
        )

    connection.execute("DELETE FROM player_match_stats WHERE match_id = ?", (match_id,))

    inserted = 0
    goals_by_team: dict[int, int] = {}
    for team_id, summary_df in team_summary.items():
        keeper_df = team_keeper.get(team_id)
        keeper_by_name = (
            {row["Player"]: row for _, row in keeper_df.iterrows()}
            if keeper_df is not None else {}
        )
        team_goals = 0
        for _, row in summary_df.iterrows():
            player_name = row.get("Player")
            if not _is_real_row(player_name):
                continue
            minutes = _na(row.get("Min"))
            if minutes is None:
                continue

            player_id = _get_or_create_player(connection, player_name)

            age_raw = _na(row.get("Age"))
            age_years = None
            if age_raw is not None:
                try:
                    age_years = int(str(age_raw).split("-")[0])
                except (ValueError, TypeError):
                    age_years = None

            values = {dest: int(_na(row.get(source)) or 0) for dest, source in FIELD_COLUMNS.items()}
            team_goals += values["goals"]

            gk_row = keeper_by_name.get(player_name)
            gk_values: dict[str, int | None] = {dest: None for dest in KEEPER_COLUMNS}
            if gk_row is not None:
                gk_values = {dest: _na(gk_row.get(source)) for dest, source in KEEPER_COLUMNS.items()}

            connection.execute(
                """INSERT INTO player_match_stats(
                    match_id, team_id, player_id, shirt_number, position, age_years, minutes,
                    goals, assists, penalties_scored, penalties_attempted, shots, shots_on_target,
                    yellow_cards, red_cards, fouls_committed, fouls_drawn, offsides, crosses,
                    tackles_won, interceptions, own_goals, penalties_won, penalties_conceded,
                    shots_on_target_against, goals_against, saves
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    match_id, team_id, player_id,
                    _na(row.get("#")), _na(row.get("Pos")), age_years, int(minutes),
                    values["goals"], values["assists"], values["penalties_scored"], values["penalties_attempted"],
                    values["shots"], values["shots_on_target"], values["yellow_cards"], values["red_cards"],
                    values["fouls_committed"], values["fouls_drawn"], values["offsides"], values["crosses"],
                    values["tackles_won"], values["interceptions"], values["own_goals"],
                    values["penalties_won"], values["penalties_conceded"],
                    gk_values["shots_on_target_against"], gk_values["goals_against"], gk_values["saves"],
                ),
            )
            inserted += 1
        goals_by_team[team_id] = team_goals

    connection.execute(
        "UPDATE match SET result_status = 'played' WHERE match_id = ? AND result_status = 'scheduled'",
        (match_id,),
    )
    for team_id, goals in goals_by_team.items():
        connection.execute(
            "UPDATE match_team SET score = ? WHERE match_id = ? AND team_id = ? AND score IS NULL",
            (goals, match_id, team_id),
        )

    return {"player_match_stats": inserted, **{f"goles_team_{k}": v for k, v in goals_by_team.items()}}


def main() -> None:
    parser = argparse.ArgumentParser(description="Carga el detalle por jugador de un partido puntual.")
    parser.add_argument("--opponent", required=True, help='Nombre del rival tal como figura en `team` (p. ej. "Banfield").')
    parser.add_argument("--date", required=True, help="Fecha del partido, formato AAAA-MM-DD.")
    parser.add_argument("--entity", default="river")
    parser.add_argument("--team-name", default="River Plate")
    parser.add_argument(
        "--export-dir", default=None,
        help="Carpeta con los .xls exportados de ese partido. Por defecto, data/match_exports/<año>.",
    )
    args = parser.parse_args()

    export_dir = Path(args.export_dir) if args.export_dir else MATCH_EXPORTS_DIR / args.date[:4]

    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            entity_team_id = connection.execute(
                "SELECT team_id FROM team WHERE name = ?", (args.team_name,)
            ).fetchone()
            if entity_team_id is None:
                raise GoldLoadError(f"No existe el equipo '{args.team_name}' en `team` — corre gold_load.py primero.")
            entity_team_id = entity_team_id[0]

            match_id, opponent_team_id = _resolve_match(connection, entity_team_id, args.opponent, args.date)
            result = load_match_report(connection, match_id, entity_team_id, opponent_team_id, export_dir)

    logger.info("Match report cargado | match_id=%s | %s", match_id, result)


if __name__ == "__main__":
    main()
