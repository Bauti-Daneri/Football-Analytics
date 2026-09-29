"""Clasificación auxiliar de exports FBref para la ingesta Bronze.

Las transformaciones se usan solamente en memoria para reconocer tablas;
los archivos publicados en Bronze se preservan byte a byte.
"""

from collections import Counter

import pandas as pd

LINK_PLACEHOLDER_COLUMNS = {"Matches", "Match Report"}


def flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    """FBref arma headers de dos niveles (ej. 'Performance' arriba de
    'Gls'). Los aplanamos a un solo nivel, prefijando con el grupo
    SOLO cuando hace falta para desambiguar -- si 'Gls' aparece una
    sola vez en la tabla, queda como 'Gls'; si aparece dos veces bajo
    grupos distintos (ej. 'Performance' y 'Per 90 Minutes'), pasan a
    'Performance_Gls' y 'Per 90 Minutes_Gls' para no pisarse."""
    if not isinstance(df.columns, pd.MultiIndex):
        return df

    bases = [str(bottom).strip() for _, bottom in df.columns]
    counts = Counter(bases)

    new_cols = []
    for (top, _), base in zip(df.columns, bases):
        top = "" if str(top).startswith("Unnamed") else str(top).strip()
        new_cols.append(f"{top}_{base}" if top and counts[base] > 1 else base)
    df = df.copy()
    df.columns = new_cols
    return df


def drop_link_placeholder_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.drop(
        columns=[c for c in df.columns if c in LINK_PLACEHOLDER_COLUMNS],
        errors="ignore",
    )
    for col in list(df.columns):
        non_null = df[col].dropna()
        if len(non_null) and non_null.isin(LINK_PLACEHOLDER_COLUMNS).all():
            df = df.drop(columns=[col])
    return df


def clean_table(df: pd.DataFrame) -> pd.DataFrame:
    """Limpieza completa: aplanar headers + sacar columnas de
    navegacion. Es la transformacion Bronze -> Silver."""
    df = flatten_columns(df)
    df = drop_link_placeholder_columns(df)
    return df


def identify_table(df: pd.DataFrame) -> str:
    """Identifica que tabla de FBref es un DataFrame por su firma de
    columnas (una vez limpio), en vez de depender de la posicion en
    la que vino en la pagina o el nombre del archivo -- eso es fragil
    porque el orden de tablas en FBref puede cambiar sin aviso."""
    clean = clean_table(df)
    cols = set(clean.columns)

    if {"Result", "GF", "GA", "Opponent", "Venue"} <= cols:
        return "resultados"
    if {"Sh", "SoT", "SoT%"} <= cols:
        return "tiros"
    if {"CrdY", "Fls", "Off", "Crs"} <= cols:
        return "disciplina"
    if any(c.endswith("GA90") for c in cols) and any(c.endswith("Saves") for c in cols):
        return "arqueros"
    if any("Mn/MP" in c for c in cols) and any("PPM" in c for c in cols):
        return "minutos_jugados"

    known_group_prefixes = {
        "Playing Time", "Performance", "Per 90 Minutes", "Standard",
        "Penalty Kicks", "Starts", "Subs", "Team Success",
    }
    competition_prefixed = [
        c for c in cols if "_" in c and c.rsplit("_", 1)[0] not in known_group_prefixes
    ]
    if competition_prefixed:
        if any(c.endswith("_Gls") for c in competition_prefixed):
            return "resumen_jugadores_por_competencia"
        if any(c.endswith("_CS") for c in competition_prefixed):
            return "resumen_arqueros_por_competencia"

    if any("Gls" in c for c in cols) and any("Starts" in c for c in cols):
        return "stats_jugadores"

    return "tabla_no_identificada"
