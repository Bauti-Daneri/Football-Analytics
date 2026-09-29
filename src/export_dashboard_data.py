"""Exporta la base Gold a un set de CSV en esquema estrella, pensado para
importar directo en Power BI / Tableau y armar los 5 dashboards del
roadmap (general, ataque, defensa, jugador individual, partido
particular). No agrega ni inventa metricas: son las mismas tablas de
`database/schema.sql` y sus vistas, con nombres de columna en español y
legibles para arrastrar directo a un visual.

Grano de cada archivo:
- dim_*.csv          una fila por entidad (dimension)
- fact_partidos.csv  un partido de River (no de ambos equipos)
- fact_jugadores_temporada.csv  un jugador, una temporada, una competencia
  (liga real O el agregado "Todas las competencias" — se distinguen con
  la columna Es_Agregado, asi el dashboard puede filtrar por una
  competencia real o por todas sin duplicar el peso de cada partido)
- fact_jugadores_partido.csv  un jugador, en un partido puntual, con los
  dos equipos (columna Condición_Equipo distingue Propio de Rival) —
  vacío hasta que se cargue al menos un partido con
  `load_match_report.py`."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.logging_config import get_logger
from src.utils.paths import BASE_DIR, DATABASE_PATH

logger = get_logger("export_dashboard_data")

OUTPUT_DIR = BASE_DIR / "reports" / "dashboard_data"

RESULT_LABELS = {1: "Ganado", 0: "Empatado", -1: "Perdido"}


class ExportError(RuntimeError):
    """Error esperado al exportar los datos para los dashboards."""


def _connect() -> sqlite3.Connection:
    if not DATABASE_PATH.exists():
        raise ExportError(
            f"No existe {DATABASE_PATH}. Corré primero `python src/gold_load.py`."
        )
    return sqlite3.connect(DATABASE_PATH)


def export_dim_season(con: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql("SELECT season_id, label AS Temporada FROM season ORDER BY label", con)
    return df


def export_dim_competition(con: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql("SELECT competition_id, name AS Competencia FROM competition ORDER BY name", con)
    df["Es_Agregado"] = df["Competencia"].eq("Todas las competencias")
    return df


def export_dim_team(con: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql("SELECT team_id, name AS Equipo FROM team ORDER BY name", con)


def export_dim_player(con: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql(
        """SELECT player_id, canonical_name AS Jugador, nationality AS Nacionalidad,
        primary_position AS Posición FROM player ORDER BY canonical_name""",
        con,
    )


def export_fact_partidos(con: sqlite3.Connection, team_name: str) -> pd.DataFrame:
    query = """
    SELECT
        s.label AS Temporada,
        c.name AS Competencia,
        m.match_date AS Fecha,
        m.round AS Jornada,
        CASE WHEN mt.venue_role = 'home' THEN 'Local' ELSE 'Visitante' END AS Condición,
        opp.name AS Rival,
        mt.score AS Goles_Favor,
        rival.score AS Goles_Contra,
        m.result_status AS Estado,
        mt.possession AS "Posesión_%",
        mt.formation AS Formación,
        m.attendance AS Asistencia,
        m.referee AS Árbitro
    FROM match m
    JOIN match_team mt ON mt.match_id = m.match_id
    JOIN team t ON t.team_id = mt.team_id AND t.name = ?
    JOIN match_team rival ON rival.match_id = m.match_id AND rival.team_id != mt.team_id
    JOIN team opp ON opp.team_id = rival.team_id
    JOIN season s ON s.season_id = m.season_id
    JOIN competition c ON c.competition_id = m.competition_id
    ORDER BY m.match_date
    """
    df = pd.read_sql(query, con, params=(team_name,))

    def _resultado(row: pd.Series) -> str:
        if row["Estado"] != "played" or pd.isna(row["Goles_Favor"]) or pd.isna(row["Goles_Contra"]):
            return "Pendiente"
        if row["Goles_Favor"] > row["Goles_Contra"]:
            return "Ganado"
        if row["Goles_Favor"] == row["Goles_Contra"]:
            return "Empatado"
        return "Perdido"

    df["Resultado"] = df.apply(_resultado, axis=1)
    df["Puntos"] = df["Resultado"].map({"Ganado": 3, "Empatado": 1, "Perdido": 0, "Pendiente": None})
    df = df.drop(columns=["Estado"])
    return df


def export_fact_jugadores_temporada(con: sqlite3.Connection, team_name: str) -> pd.DataFrame:
    query = """
    SELECT
        s.label AS Temporada,
        c.name AS Competencia,
        p.canonical_name AS Jugador,
        p.primary_position AS Posición,
        pss.age_years AS Edad,
        pss.appearances AS Partidos_Jugados,
        pss.starts AS Titular,
        pss.minutes AS Minutos,
        pss.goals AS Goles,
        pss.assists AS Asistencias,
        (pss.goals + pss.assists) AS Goles_y_Asistencias,
        pss.penalties_scored AS Penales_Convertidos,
        pss.penalties_attempted AS Penales_Intentados,
        pss.yellow_cards AS Tarjetas_Amarillas,
        pss.red_cards AS Tarjetas_Rojas,
        pss.shots AS Tiros,
        pss.shots_on_target AS Tiros_al_Arco,
        pss.fouls_committed AS Faltas_Cometidas,
        pss.fouls_drawn AS Faltas_Recibidas,
        pss.offsides AS Offsides,
        pss.crosses AS Centros,
        pss.tackles_won AS Tackles_Ganados,
        pss.interceptions AS Intercepciones,
        pss.shots_on_target_against AS Tiros_al_Arco_Recibidos,
        pss.goals_against AS Goles_Recibidos,
        pss.saves AS Atajadas,
        pss.clean_sheets AS Vallas_Invictas
    FROM player_season_stats pss
    JOIN player p ON p.player_id = pss.player_id
    JOIN team t ON t.team_id = pss.team_id AND t.name = ?
    JOIN season s ON s.season_id = pss.season_id
    JOIN competition c ON c.competition_id = pss.competition_id
    ORDER BY s.label, c.name, pss.goals DESC
    """
    df = pd.read_sql(query, con, params=(team_name,))
    df["Es_Agregado"] = df["Competencia"].eq("Todas las competencias")

    def _safe_ratio(numerator: pd.Series, denominator: pd.Series, factor: float = 1.0) -> pd.Series:
        num = numerator.astype("float64")
        den = denominator.astype("float64").replace(0, float("nan"))
        return (num / den * factor).round(2)

    df["Goles_por_90"] = _safe_ratio(df["Goles"], df["Minutos"], 90)
    df["Asistencias_por_90"] = _safe_ratio(df["Asistencias"], df["Minutos"], 90)
    df["%_Tiros_al_Arco"] = _safe_ratio(df["Tiros_al_Arco"], df["Tiros"], 100)
    df["%_Conversión_de_Gol"] = _safe_ratio(df["Goles"], df["Tiros"], 100)
    df["%_Atajadas"] = _safe_ratio(df["Atajadas"], df["Tiros_al_Arco_Recibidos"], 100)
    return df


def export_fact_jugadores_partido(con: sqlite3.Connection, team_name: str) -> pd.DataFrame:
    """Detalle jugador-a-jugador de los partidos puntuales cargados con
    `load_match_report.py` (los dos equipos del partido, no solo el
    propio). Queda vacío hasta que se cargue al menos un partido así —
    FBref no trae esto por temporada completa, hay que sumarlo partido
    por partido (ver limitación en `gold_load.py`)."""
    query = """
    SELECT
        s.label AS Temporada,
        c.name AS Competencia,
        m.match_date AS Fecha,
        t_row.name AS Equipo,
        t_other.name AS Rival,
        p.canonical_name AS Jugador,
        pms.shirt_number AS Camiseta,
        pms.position AS Posición,
        pms.age_years AS Edad,
        pms.minutes AS Minutos,
        pms.goals AS Goles,
        pms.assists AS Asistencias,
        pms.shots AS Tiros,
        pms.shots_on_target AS Tiros_al_Arco,
        pms.yellow_cards AS Tarjetas_Amarillas,
        pms.red_cards AS Tarjetas_Rojas,
        pms.fouls_committed AS Faltas_Cometidas,
        pms.fouls_drawn AS Faltas_Recibidas,
        pms.offsides AS Offsides,
        pms.crosses AS Centros,
        pms.tackles_won AS Tackles_Ganados,
        pms.interceptions AS Intercepciones,
        pms.own_goals AS Goles_en_Contra,
        pms.penalties_won AS Penales_Ganados,
        pms.penalties_conceded AS Penales_Concedidos,
        pms.shots_on_target_against AS Tiros_al_Arco_Recibidos,
        pms.goals_against AS Goles_Recibidos,
        pms.saves AS Atajadas
    FROM player_match_stats pms
    JOIN match m ON m.match_id = pms.match_id
    JOIN season s ON s.season_id = m.season_id
    JOIN competition c ON c.competition_id = m.competition_id
    JOIN team t_row ON t_row.team_id = pms.team_id
    JOIN match_team mt_other ON mt_other.match_id = m.match_id AND mt_other.team_id != pms.team_id
    JOIN team t_other ON t_other.team_id = mt_other.team_id
    JOIN player p ON p.player_id = pms.player_id
    WHERE ? IN (t_row.name, t_other.name)
    ORDER BY m.match_date, Equipo, pms.goals DESC
    """
    df = pd.read_sql(query, con, params=(team_name,))
    df["Condición_Equipo"] = df["Equipo"].eq(team_name).map({True: "Propio", False: "Rival"})

    def _safe_ratio(numerator: pd.Series, denominator: pd.Series, factor: float = 1.0) -> pd.Series:
        num = numerator.astype("float64")
        den = denominator.astype("float64").replace(0, float("nan"))
        return (num / den * factor).round(2)

    df["%_Tiros_al_Arco"] = _safe_ratio(df["Tiros_al_Arco"], df["Tiros"], 100)
    df["%_Conversión_de_Gol"] = _safe_ratio(df["Goles"], df["Tiros"], 100)
    df["%_Atajadas"] = _safe_ratio(df["Atajadas"], df["Tiros_al_Arco_Recibidos"], 100)
    return df


def export_all(entity: str = "river", team_name: str = "River Plate") -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with closing(_connect()) as con:
        tables = {
            "dim_temporada": export_dim_season(con),
            "dim_competencia": export_dim_competition(con),
            "dim_equipo": export_dim_team(con),
            "dim_jugador": export_dim_player(con),
            "fact_partidos": export_fact_partidos(con, team_name),
            "fact_jugadores_temporada": export_fact_jugadores_temporada(con, team_name),
            "fact_jugadores_partido": export_fact_jugadores_partido(con, team_name),
        }
    for name, df in tables.items():
        destination = OUTPUT_DIR / f"{name}.csv"
        df.to_csv(destination, index=False, encoding="utf-8-sig")
        logger.info("Exportado %s (%s filas) -> %s", name, len(df), destination)
    return OUTPUT_DIR


def main() -> None:
    parser = argparse.ArgumentParser(description="Exporta la base Gold a CSV para Power BI / Tableau.")
    parser.add_argument("--entity", default="river")
    parser.add_argument("--team-name", default="River Plate")
    args = parser.parse_args()
    export_all(args.entity, args.team_name)


if __name__ == "__main__":
    main()
