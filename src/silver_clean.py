"""Capa Silver: limpieza estructural de los XLS crudos de Bronze.

Toma la corrida mas reciente de Bronze para cada temporada, aplana los
headers multi-nivel de FBref, descarta columnas de navegacion (ver
src/utils/cleaning.py) y normaliza texto. No inventa datos ni imputa
nulos: solo estructura lo que Bronze ya valido. Publica su propia
corrida versionada en Silver, con el mismo esquema de manifest + hash
que Bronze (una corrida sin cambios reutiliza la anterior en vez de
duplicar).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.cleaning import clean_table
from src.utils.logging_config import get_logger
from src.utils.paths import BRONZE_DIR, SILVER_DIR, ensure_project_dirs

logger = get_logger("silver_clean")


class SilverCleanError(RuntimeError):
    """Error esperado de limpieza o validacion tecnica de Silver."""


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _latest_bronze_run(entity: str, season: str) -> Path:
    season_dir = BRONZE_DIR / entity / season
    if not season_dir.exists():
        raise SilverCleanError(f"No existe Bronze para {entity}/{season} en {season_dir}.")
    run_dirs = sorted(
        p for p in season_dir.iterdir() if p.is_dir() and (p / "manifest.json").exists()
    )
    if not run_dirs:
        raise SilverCleanError(f"No hay corridas publicadas de Bronze en {season_dir}.")
    return run_dirs[-1]


def normalize_text_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Prolijidad de texto unicamente: espacios y celdas vacias/'nan'/
    'None' literales que a veces deja el HTML de FBref. No completa
    nulos con valores inventados."""
    df = df.copy()
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].astype(str).str.strip()
            df[col] = df[col].replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})
    return df


def _artifact_record(path: Path, staging: Path, logical_name: str) -> dict[str, Any]:
    content = path.read_bytes()
    return {
        "logical_name": logical_name,
        "path": path.relative_to(staging).as_posix(),
        "bytes": len(content),
        "sha256": _sha256_bytes(content),
    }


def _dataset_digest(artifacts: list[dict[str, Any]]) -> str:
    identity = sorted(
        ({"logical_name": item["logical_name"], "sha256": item["sha256"]} for item in artifacts),
        key=lambda item: item["logical_name"],
    )
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256_bytes(encoded)


def _latest_manifest(dataset_dir: Path) -> dict[str, Any] | None:
    for path in sorted(dataset_dir.glob("*/manifest.json"), reverse=True):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


def _publish_staging(
    staging: Path, entity: str, season: str, artifacts: list[dict[str, Any]], bronze_run_id: str
) -> Path:
    dataset_dir = SILVER_DIR / entity / season
    digest = _dataset_digest(artifacts)
    previous = _latest_manifest(dataset_dir)
    if previous and previous.get("dataset_sha256") == digest:
        shutil.rmtree(staging)
        existing = dataset_dir / previous["run_id"]
        logger.info("Silver sin cambios; se conserva %s", existing)
        return existing

    created_at = _utc_now()
    run_id = created_at.strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    output_dir = dataset_dir / run_id
    if output_dir.exists():
        shutil.rmtree(staging)
        raise SilverCleanError(f"La corrida {output_dir} ya existe; no se sobrescribira.")

    _write_json(staging / "manifest.json", {
        "manifest_version": 1,
        "run_id": run_id,
        "created_at_utc": created_at.isoformat(),
        "entity": entity,
        "source": "bronze",
        "bronze_run_id": bronze_run_id,
        "scope": {"kind": "season", "season": season},
        "silver_policy": "flatten_headers_drop_navigation_normalize_text",
        "dataset_sha256": digest,
        "artifacts": artifacts,
    })
    staging.rename(output_dir)
    logger.info("Silver publicado | artifacts=%s | %s", len(artifacts), output_dir)
    return output_dir


def clean_season(entity: str = "river", season: str = "2026") -> Path:
    ensure_project_dirs()
    bronze_run = _latest_bronze_run(entity, season)
    xls_files = sorted(bronze_run.glob("*.xls"))
    if not xls_files:
        raise SilverCleanError(f"No hay .xls en la corrida de Bronze {bronze_run}.")

    dataset_dir = SILVER_DIR / entity / season
    dataset_dir.mkdir(parents=True, exist_ok=True)
    staging = dataset_dir / f".staging-{uuid.uuid4().hex}"
    staging.mkdir(parents=True)
    try:
        artifacts: list[dict[str, Any]] = []
        for xls_path in xls_files:
            logical_name = xls_path.stem
            try:
                raw_df = pd.read_html(xls_path, encoding="utf-8")[0]
            except Exception as exc:
                raise SilverCleanError(f"No se puede leer {xls_path.name}: {exc}") from exc
            cleaned = clean_table(raw_df)
            cleaned = normalize_text_columns(cleaned)
            destination = staging / f"{logical_name}.csv"
            cleaned.to_csv(destination, index=False, encoding="utf-8-sig")
            artifacts.append(_artifact_record(destination, staging, logical_name))
        return _publish_staging(staging, entity, season, artifacts, bronze_run.name)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def clean_all_seasons(entity: str = "river") -> dict[str, Path]:
    ensure_project_dirs()
    entity_dir = BRONZE_DIR / entity
    if not entity_dir.exists():
        raise SilverCleanError(f"No hay datos de Bronze para la entidad '{entity}'.")
    seasons = sorted(p.name for p in entity_dir.iterdir() if p.is_dir())
    if not seasons:
        raise SilverCleanError(f"No hay temporadas publicadas en Bronze para '{entity}'.")
    return {season: clean_season(entity, season) for season in seasons}


def main() -> None:
    parser = argparse.ArgumentParser(description="Limpieza Silver de los XLS crudos de Bronze.")
    parser.add_argument("--entity", default="river")
    args = parser.parse_args()
    published = clean_all_seasons(args.entity)
    for season, path in published.items():
        logger.info("Temporada %s -> %s", season, path)


if __name__ == "__main__":
    main()
