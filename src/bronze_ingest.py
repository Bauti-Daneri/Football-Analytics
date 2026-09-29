"""Capa Bronze: ingesta manual, fiel y auditable de exports de FBref.

FBref se usa exclusivamente mediante su opcion visible "Share & Export ->
Get as Excel Workbook". El programa no hace scraping ni intenta superar
Cloudflare. Bronze clasifica los archivos en memoria, pero publica copias XLS
byte a byte: no limpia, normaliza, filtra ni elimina duplicados.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
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

from src.utils.cleaning import clean_table, identify_table
from src.utils.logging_config import get_logger
from src.utils.paths import BRONZE_DIR, MANUAL_EXPORTS_DIR, ensure_project_dirs

logger = get_logger("bronze_ingest")

REQUIRED_MANUAL_TABLES = {
    "resultados",
    "arqueros",
    "tiros",
    "minutos_jugados",
    "disciplina",
    "resumen_jugadores_por_competencia",
    "resumen_arqueros_por_competencia",
    "stats_jugadores_liga",
    "stats_jugadores_todas_competencias",
}
YEAR_PATTERN = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


class BronzeIngestError(RuntimeError):
    """Error esperado de adquisicion o validacion tecnica de Bronze."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


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
    staging: Path,
    entity: str,
    season: str,
    artifacts: list[dict[str, Any]],
) -> Path:
    dataset_dir = BRONZE_DIR / entity / season
    digest = _dataset_digest(artifacts)
    previous = _latest_manifest(dataset_dir)
    if previous and previous.get("dataset_sha256") == digest:
        shutil.rmtree(staging)
        existing = dataset_dir / previous["run_id"]
        logger.info("Bronze sin cambios; se conserva %s", existing)
        return existing

    created_at = _utc_now()
    run_id = created_at.strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    output_dir = dataset_dir / run_id
    if output_dir.exists():
        shutil.rmtree(staging)
        raise BronzeIngestError(f"La corrida {output_dir} ya existe; no se sobrescribira.")

    _write_json(staging / "manifest.json", {
        "manifest_version": 1,
        "run_id": run_id,
        "created_at_utc": created_at.isoformat(),
        "entity": entity,
        "source": "fbref-manual-export",
        "scope": {"kind": "season", "season": season},
        "bronze_policy": "raw_xls_preserved_byte_for_byte_without_cleaning",
        "dataset_sha256": digest,
        "artifacts": artifacts,
        "source_details": {
            "provider": "FBref",
            "acquisition": "manual Share & Export -> Get as Excel Workbook",
            "tables": len(artifacts),
        },
    })
    staging.rename(output_dir)
    logger.info("Bronze publicado | artifacts=%s | %s", len(artifacts), output_dir)
    return output_dir


def _manual_export_mapping(files: list[Path]) -> dict[str, Path]:
    files = sorted(files)
    if not files:
        raise BronzeIngestError("No hay exports .xls para la temporada.")

    mapping: dict[str, Path] = {}
    standards: list[tuple[Path, pd.DataFrame]] = []
    for path in files:
        try:
            raw_df = pd.read_html(path, encoding="utf-8")[0]
        except Exception as exc:
            raise BronzeIngestError(f"No se puede leer {path.name}: {exc}") from exc
        if raw_df.empty:
            raise BronzeIngestError(f"El export {path.name} esta vacio.")
        name = identify_table(raw_df)
        if name == "tabla_no_identificada":
            raise BronzeIngestError(f"No se pudo identificar {path.name}.")
        if name == "stats_jugadores":
            standards.append((path, raw_df))
        elif name in mapping:
            raise BronzeIngestError(f"Hay dos exports identificados como '{name}'.")
        else:
            mapping[name] = path

    if len(standards) != 2:
        raise BronzeIngestError(
            "Se esperaban dos tablas estandar: liga y todas las competiciones."
        )

    def total_minutes(candidate: tuple[Path, pd.DataFrame]) -> float:
        cleaned = clean_table(candidate[1])
        column = next((c for c in cleaned.columns if c.endswith("_Min") or c == "Min"), None)
        return float(pd.to_numeric(cleaned[column], errors="coerce").sum()) if column else 0.0

    ordered = sorted(standards, key=total_minutes)
    mapping["stats_jugadores_liga"] = ordered[0][0]
    mapping["stats_jugadores_todas_competencias"] = ordered[1][0]

    missing = sorted(REQUIRED_MANUAL_TABLES - set(mapping))
    extra = sorted(set(mapping) - REQUIRED_MANUAL_TABLES)
    if missing or extra:
        raise BronzeIngestError(
            f"Exports incompletos. Faltan={missing}; inesperados={extra}"
        )
    return mapping


def _year_from_path(path: Path) -> str:
    parent_years = YEAR_PATTERN.findall(path.parent.name)
    filename_years = YEAR_PATTERN.findall(path.stem)
    years = sorted(set(parent_years + filename_years))
    if len(years) != 1:
        raise BronzeIngestError(
            f"No se puede determinar un unico año para {path}. "
            "Inclui el año en el nombre (ej. 2019_archivo.xls) o en su carpeta padre."
        )
    return years[0]


def _discover_exports_by_season() -> dict[str, list[Path]]:
    """Agrupa todos los XLS recibidos por el año indicado en ruta o nombre."""
    files = sorted(MANUAL_EXPORTS_DIR.rglob("*.xls")) if MANUAL_EXPORTS_DIR.exists() else []
    if not files:
        raise BronzeIngestError(
            f"No hay exports .xls en {MANUAL_EXPORTS_DIR}."
        )
    grouped: dict[str, list[Path]] = {}
    errors = []
    for path in files:
        try:
            year = _year_from_path(path)
        except BronzeIngestError as exc:
            errors.append(str(exc))
            continue
        grouped.setdefault(year, []).append(path)
    if errors:
        raise BronzeIngestError("\n".join(errors))
    return dict(sorted(grouped.items()))


def ingest_manual(
    entity: str = "river",
) -> dict[str, Path]:
    ensure_project_dirs()
    grouped = _discover_exports_by_season()
    mappings: dict[str, dict[str, Path]] = {}
    for season, files in grouped.items():
        mappings[season] = _manual_export_mapping(files)

    published: dict[str, Path] = {}
    try:
        for current_season, mapping in mappings.items():
            dataset_dir = BRONZE_DIR / entity / current_season
            dataset_dir.mkdir(parents=True, exist_ok=True)
            staging = dataset_dir / f".staging-{uuid.uuid4().hex}"
            staging.mkdir()
            artifacts = []
            for logical_name, source_path in sorted(mapping.items()):
                destination = staging / f"{logical_name}.xls"
                shutil.copyfile(source_path, destination)
                artifacts.append(_artifact_record(destination, staging, logical_name))
            published[current_season] = _publish_staging(
                staging, entity, current_season, artifacts
            )
        return published
    except Exception:
        for path in (BRONZE_DIR / entity).glob("*/.staging-*"):
            shutil.rmtree(path)
        raise


def ingest(
    entity: str = "river",
    source: str = "manual",
) -> dict[str, Path]:
    if source != "manual":
        raise BronzeIngestError("La unica fuente habilitada es 'manual'.")
    return ingest_manual(entity)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingesta manual de datos crudos de FBref.")
    parser.add_argument("--entity", default="river")
    parser.add_argument("--source", choices=("manual",), default="manual")
    args = parser.parse_args()
    ingest(args.entity, args.source)


if __name__ == "__main__":
    main()
