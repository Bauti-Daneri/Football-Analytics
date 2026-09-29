import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.bronze_ingest import BronzeIngestError, REQUIRED_MANUAL_TABLES, ingest_manual


def create_fake_season(root: Path, season: str, flat: bool = False) -> dict[str, Path]:
    season_dir = root if flat else root / season
    season_dir.mkdir(parents=True, exist_ok=True)
    mapping = {}
    for index, logical_name in enumerate(sorted(REQUIRED_MANUAL_TABLES)):
        prefix = f"{season}_" if flat else ""
        path = season_dir / f"{prefix}source_{index}.xls"
        path.write_bytes(f"{season}:{logical_name}".encode())
        mapping[logical_name] = path
    return mapping


class BronzeTests(unittest.TestCase):
    def test_no_available_seasons_is_clear(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with (
                patch("src.bronze_ingest.BRONZE_DIR", root / "bronze"),
                patch("src.bronze_ingest.MANUAL_EXPORTS_DIR", root / "exports"),
                patch("src.bronze_ingest.ensure_project_dirs"),
            ):
                with self.assertRaisesRegex(BronzeIngestError, "No hay exports"):
                    ingest_manual()

    def test_uses_every_available_season_and_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            exports = root / "exports"
            mappings = {
                "2025": create_fake_season(exports, "2025"),
                "2026": create_fake_season(exports, "2026"),
            }

            def mapping_for(files: list[Path]):
                return mappings[files[0].parent.name]

            with (
                patch("src.bronze_ingest.BRONZE_DIR", root / "bronze"),
                patch("src.bronze_ingest.MANUAL_EXPORTS_DIR", exports),
                patch("src.bronze_ingest.ensure_project_dirs"),
                patch("src.bronze_ingest._manual_export_mapping", side_effect=mapping_for),
            ):
                output = ingest_manual()
                repeated = ingest_manual()

            self.assertEqual(output, repeated)
            self.assertEqual({"2025", "2026"}, set(output))
            for season, mapping in mappings.items():
                manifest = json.loads((output[season] / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(season, manifest["scope"]["season"])
                self.assertEqual(9, len(manifest["artifacts"]))
                for logical_name, source in mapping.items():
                    destination = output[season] / f"{logical_name}.xls"
                    self.assertEqual(source.read_bytes(), destination.read_bytes())

    def test_single_available_season_is_enough(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            exports = root / "exports"
            mapping = create_fake_season(exports, "2026", flat=True)
            with (
                patch("src.bronze_ingest.BRONZE_DIR", root / "bronze"),
                patch("src.bronze_ingest.MANUAL_EXPORTS_DIR", exports),
                patch("src.bronze_ingest.ensure_project_dirs"),
                patch("src.bronze_ingest._manual_export_mapping", return_value=mapping),
            ):
                output = ingest_manual()
            manifest = json.loads((output["2026"] / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual("2026", manifest["scope"]["season"])

    def test_flat_file_without_year_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            exports = root / "exports"
            exports.mkdir()
            (exports / "sportsref_download.xls").write_bytes(b"raw")
            with (
                patch("src.bronze_ingest.BRONZE_DIR", root / "bronze"),
                patch("src.bronze_ingest.MANUAL_EXPORTS_DIR", exports),
                patch("src.bronze_ingest.ensure_project_dirs"),
            ):
                with self.assertRaisesRegex(BronzeIngestError, "determinar"):
                    ingest_manual()


if __name__ == "__main__":
    unittest.main()
