import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from src.create_database import EXPECTED_SCHEMA_VERSION, create_database


class DatabaseTests(unittest.TestCase):
    def test_creates_expected_schema_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            database_path = Path(temp) / "test.db"
            create_database(database_path)
            create_database(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                version = connection.execute(
                    "SELECT MAX(version) FROM schema_version"
                ).fetchone()[0]
                views = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'view'"
                    )
                }

            self.assertTrue(
                {
                    "team", "competition", "season", "player", "player_alias",
                    "match", "match_team", "player_match_stats",
                    "player_season_stats", "data_load", "data_quality_result",
                }.issubset(tables)
            )
            self.assertNotIn("team_match_stats", tables)
            self.assertNotIn("goalkeeper_match_stats", tables)
            self.assertNotIn("goalkeeper_season_stats", tables)
            self.assertEqual(
                {
                    "vw_team_match_totals",
                    "vw_player_match_metrics",
                    "vw_player_season_metrics",
                },
                views,
            )
            self.assertEqual(EXPECTED_SCHEMA_VERSION, version)

    def test_player_stats_require_a_team_from_the_match(self):
        with tempfile.TemporaryDirectory() as temp:
            database_path = Path(temp) / "test.db"
            create_database(database_path)
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                self._seed_match(connection)
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """INSERT INTO player_match_stats(
                            match_id, team_id, player_id, minutes
                        ) VALUES ('m1', 3, 1, 90)"""
                    )

    def test_rejects_impossible_goalkeeper_totals(self):
        with tempfile.TemporaryDirectory() as temp:
            database_path = Path(temp) / "test.db"
            create_database(database_path)
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                self._seed_match(connection)
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """INSERT INTO player_match_stats(
                            match_id, team_id, player_id, minutes,
                            shots_on_target_against, goals_against, saves
                        ) VALUES ('m1', 1, 1, 90, 5, 2, 2)"""
                    )

    def test_accepts_complete_goalkeeper_stats_in_player_row(self):
        with tempfile.TemporaryDirectory() as temp:
            database_path = Path(temp) / "test.db"
            create_database(database_path)
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                self._seed_match(connection)
                connection.execute(
                    """INSERT INTO player_match_stats(
                        match_id, team_id, player_id, minutes,
                        shots_on_target_against, goals_against, saves
                    ) VALUES ('m1', 1, 1, 90, 5, 2, 3)"""
                )

    def test_accepts_partial_goalkeeper_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            database_path = Path(temp) / "test.db"
            create_database(database_path)
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("INSERT INTO team(name) VALUES ('River Plate')")
                connection.execute("INSERT INTO competition(name) VALUES ('Sudamericana')")
                connection.execute("INSERT INTO season(label) VALUES ('2026')")
                connection.execute("INSERT INTO player(canonical_name) VALUES ('Arquero')")
                connection.execute(
                    """INSERT INTO player_season_stats(
                        season_id, competition_id, team_id, player_id,
                        goals_against, clean_sheets
                    ) VALUES (1, 1, 1, 1, 3, 2)"""
                )

    @staticmethod
    def _seed_match(connection: sqlite3.Connection) -> None:
        connection.executemany(
            "INSERT INTO team(name) VALUES (?)",
            [("River Plate",), ("Banfield",), ("Boca Juniors",)],
        )
        connection.execute(
            "INSERT INTO competition(name) VALUES ('Liga Profesional Argentina')"
        )
        connection.execute("INSERT INTO season(label) VALUES ('2026')")
        connection.execute(
            "INSERT INTO player(canonical_name) VALUES ('Santiago Beltrán')"
        )
        connection.execute(
            """INSERT INTO match(
                match_id, match_date, season_id, competition_id, result_status
            ) VALUES ('m1', '2026-08-30', 1, 1, 'played')"""
        )
        connection.executemany(
            """INSERT INTO match_team(match_id, team_id, venue_role, score)
            VALUES ('m1', ?, ?, ?)""",
            [(1, "home", 3), (2, "away", 2)],
        )


if __name__ == "__main__":
    unittest.main()
