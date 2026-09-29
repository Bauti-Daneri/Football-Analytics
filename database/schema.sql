PRAGMA foreign_keys = ON;

CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE data_load (
    load_id INTEGER PRIMARY KEY,
    layer TEXT NOT NULL CHECK (layer IN ('bronze', 'silver', 'gold')),
    source_name TEXT NOT NULL,
    source_path TEXT,
    source_sha256 TEXT,
    loaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL CHECK (status IN ('started', 'completed', 'failed')),
    row_count INTEGER CHECK (row_count IS NULL OR row_count >= 0),
    notes TEXT
);

CREATE TABLE team (
    team_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    country TEXT
);

CREATE TABLE competition (
    competition_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    country TEXT
);

CREATE TABLE season (
    season_id INTEGER PRIMARY KEY,
    label TEXT NOT NULL UNIQUE,
    start_date TEXT,
    end_date TEXT,
    CHECK (start_date IS NULL OR end_date IS NULL OR start_date <= end_date)
);

CREATE TABLE player (
    player_id INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    nationality TEXT,
    primary_position TEXT
);

CREATE TABLE player_alias (
    source_name TEXT NOT NULL,
    source_player_name TEXT NOT NULL,
    player_id INTEGER NOT NULL REFERENCES player(player_id),
    PRIMARY KEY (source_name, source_player_name)
);

CREATE TABLE match (
    match_id TEXT PRIMARY KEY,
    match_date TEXT NOT NULL,
    kickoff_time TEXT,
    season_id INTEGER NOT NULL REFERENCES season(season_id),
    competition_id INTEGER NOT NULL REFERENCES competition(competition_id),
    round TEXT,
    result_status TEXT NOT NULL DEFAULT 'scheduled'
        CHECK (result_status IN ('scheduled', 'played', 'postponed', 'cancelled')),
    attendance INTEGER CHECK (attendance IS NULL OR attendance >= 0),
    referee TEXT,
    notes TEXT,
    source_load_id INTEGER REFERENCES data_load(load_id)
);

CREATE TABLE match_team (
    match_id TEXT NOT NULL REFERENCES match(match_id) ON DELETE CASCADE,
    team_id INTEGER NOT NULL REFERENCES team(team_id),
    venue_role TEXT NOT NULL CHECK (venue_role IN ('home', 'away')),
    score INTEGER CHECK (score IS NULL OR score >= 0),
    possession REAL CHECK (possession IS NULL OR possession BETWEEN 0 AND 100),
    formation TEXT,
    captain_player_id INTEGER REFERENCES player(player_id),
    PRIMARY KEY (match_id, team_id),
    UNIQUE (match_id, venue_role)
);

CREATE TABLE player_match_stats (
    match_id TEXT NOT NULL,
    team_id INTEGER NOT NULL,
    player_id INTEGER NOT NULL REFERENCES player(player_id),
    shirt_number INTEGER,
    position TEXT,
    age_years INTEGER CHECK (age_years IS NULL OR age_years BETWEEN 14 AND 60),
    minutes INTEGER NOT NULL CHECK (minutes BETWEEN 0 AND 130),
    goals INTEGER NOT NULL DEFAULT 0 CHECK (goals >= 0),
    assists INTEGER NOT NULL DEFAULT 0 CHECK (assists >= 0),
    penalties_scored INTEGER NOT NULL DEFAULT 0 CHECK (penalties_scored >= 0),
    penalties_attempted INTEGER NOT NULL DEFAULT 0 CHECK (penalties_attempted >= 0),
    shots INTEGER NOT NULL DEFAULT 0 CHECK (shots >= 0),
    shots_on_target INTEGER NOT NULL DEFAULT 0 CHECK (shots_on_target >= 0),
    yellow_cards INTEGER NOT NULL DEFAULT 0 CHECK (yellow_cards >= 0),
    red_cards INTEGER NOT NULL DEFAULT 0 CHECK (red_cards >= 0),
    fouls_committed INTEGER NOT NULL DEFAULT 0 CHECK (fouls_committed >= 0),
    fouls_drawn INTEGER NOT NULL DEFAULT 0 CHECK (fouls_drawn >= 0),
    offsides INTEGER NOT NULL DEFAULT 0 CHECK (offsides >= 0),
    crosses INTEGER NOT NULL DEFAULT 0 CHECK (crosses >= 0),
    tackles_won INTEGER NOT NULL DEFAULT 0 CHECK (tackles_won >= 0),
    interceptions INTEGER NOT NULL DEFAULT 0 CHECK (interceptions >= 0),
    own_goals INTEGER NOT NULL DEFAULT 0 CHECK (own_goals >= 0),
    penalties_won INTEGER NOT NULL DEFAULT 0 CHECK (penalties_won >= 0),
    penalties_conceded INTEGER NOT NULL DEFAULT 0 CHECK (penalties_conceded >= 0),
    shots_on_target_against INTEGER
        CHECK (shots_on_target_against IS NULL OR shots_on_target_against >= 0),
    goals_against INTEGER CHECK (goals_against IS NULL OR goals_against >= 0),
    saves INTEGER CHECK (saves IS NULL OR saves >= 0),
    source_load_id INTEGER REFERENCES data_load(load_id),
    PRIMARY KEY (match_id, team_id, player_id),
    FOREIGN KEY (match_id, team_id)
        REFERENCES match_team(match_id, team_id) ON DELETE CASCADE,
    CHECK (shots_on_target <= shots),
    CHECK (penalties_scored <= penalties_attempted),
    CHECK (
        shots_on_target_against IS NULL OR goals_against IS NULL OR saves IS NULL
        OR goals_against + saves = shots_on_target_against
    )
);

CREATE TABLE player_season_stats (
    season_id INTEGER NOT NULL REFERENCES season(season_id),
    competition_id INTEGER NOT NULL REFERENCES competition(competition_id),
    team_id INTEGER NOT NULL REFERENCES team(team_id),
    player_id INTEGER NOT NULL REFERENCES player(player_id),
    shirt_number INTEGER,
    age_years INTEGER CHECK (age_years IS NULL OR age_years BETWEEN 14 AND 60),
    appearances INTEGER CHECK (appearances IS NULL OR appearances >= 0),
    starts INTEGER CHECK (starts IS NULL OR starts >= 0),
    minutes INTEGER CHECK (minutes IS NULL OR minutes >= 0),
    goals INTEGER CHECK (goals IS NULL OR goals >= 0),
    assists INTEGER CHECK (assists IS NULL OR assists >= 0),
    penalties_scored INTEGER CHECK (penalties_scored IS NULL OR penalties_scored >= 0),
    penalties_attempted INTEGER CHECK (penalties_attempted IS NULL OR penalties_attempted >= 0),
    shots INTEGER CHECK (shots IS NULL OR shots >= 0),
    shots_on_target INTEGER CHECK (shots_on_target IS NULL OR shots_on_target >= 0),
    yellow_cards INTEGER CHECK (yellow_cards IS NULL OR yellow_cards >= 0),
    red_cards INTEGER CHECK (red_cards IS NULL OR red_cards >= 0),
    fouls_committed INTEGER CHECK (fouls_committed IS NULL OR fouls_committed >= 0),
    fouls_drawn INTEGER CHECK (fouls_drawn IS NULL OR fouls_drawn >= 0),
    offsides INTEGER CHECK (offsides IS NULL OR offsides >= 0),
    crosses INTEGER CHECK (crosses IS NULL OR crosses >= 0),
    tackles_won INTEGER CHECK (tackles_won IS NULL OR tackles_won >= 0),
    interceptions INTEGER CHECK (interceptions IS NULL OR interceptions >= 0),
    shots_on_target_against INTEGER
        CHECK (shots_on_target_against IS NULL OR shots_on_target_against >= 0),
    goals_against INTEGER CHECK (goals_against IS NULL OR goals_against >= 0),
    saves INTEGER CHECK (saves IS NULL OR saves >= 0),
    clean_sheets INTEGER CHECK (clean_sheets IS NULL OR clean_sheets >= 0),
    source_load_id INTEGER REFERENCES data_load(load_id),
    PRIMARY KEY (season_id, competition_id, team_id, player_id),
    CHECK (
        penalties_scored IS NULL OR penalties_attempted IS NULL
        OR penalties_scored <= penalties_attempted
    ),
    CHECK (shots_on_target IS NULL OR shots IS NULL OR shots_on_target <= shots),
    CHECK (
        shots_on_target_against IS NULL OR goals_against IS NULL OR saves IS NULL
        OR goals_against + saves = shots_on_target_against
    )
);

CREATE TABLE data_quality_result (
    quality_result_id INTEGER PRIMARY KEY,
    load_id INTEGER REFERENCES data_load(load_id),
    check_name TEXT NOT NULL,
    table_name TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error')),
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    observed_value TEXT,
    expected_value TEXT,
    details TEXT,
    checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_match_date ON match(match_date);
CREATE INDEX idx_match_competition ON match(competition_id, season_id);
CREATE INDEX idx_match_team_team ON match_team(team_id, match_id);
CREATE INDEX idx_player_alias_player ON player_alias(player_id);
CREATE INDEX idx_player_match_player ON player_match_stats(player_id, match_id);
CREATE INDEX idx_player_season_player
    ON player_season_stats(player_id, season_id);

CREATE VIEW vw_team_match_totals AS
SELECT
    match_id,
    team_id,
    SUM(goals) AS goals,
    SUM(assists) AS assists,
    SUM(shots) AS shots,
    SUM(shots_on_target) AS shots_on_target,
    SUM(yellow_cards) AS yellow_cards,
    SUM(red_cards) AS red_cards,
    SUM(fouls_committed) AS fouls_committed,
    SUM(fouls_drawn) AS fouls_drawn,
    SUM(offsides) AS offsides,
    SUM(crosses) AS crosses,
    SUM(tackles_won) AS tackles_won,
    SUM(interceptions) AS interceptions
FROM player_match_stats
GROUP BY match_id, team_id;

CREATE VIEW vw_player_match_metrics AS
SELECT
    player_match_stats.*,
    goals + assists AS goal_contributions,
    goals - penalties_scored AS non_penalty_goals,
    CASE WHEN minutes > 0 THEN goals * 90.0 / minutes END AS goals_per_90,
    CASE WHEN minutes > 0 THEN assists * 90.0 / minutes END AS assists_per_90,
    CASE WHEN minutes > 0 THEN shots * 90.0 / minutes END AS shots_per_90,
    CASE WHEN shots > 0 THEN shots_on_target * 100.0 / shots END
        AS shot_accuracy_percentage,
    CASE WHEN shots > 0 THEN goals * 100.0 / shots END
        AS shot_conversion_percentage,
    CASE WHEN penalties_attempted > 0
        THEN penalties_scored * 100.0 / penalties_attempted
    END AS penalty_conversion_percentage,
    CASE WHEN shots_on_target_against > 0
        THEN saves * 100.0 / shots_on_target_against
    END AS save_percentage
FROM player_match_stats;

CREATE VIEW vw_player_season_metrics AS
SELECT
    player_season_stats.*,
    goals + assists AS goal_contributions,
    goals - penalties_scored AS non_penalty_goals,
    CASE WHEN minutes > 0 THEN goals * 90.0 / minutes END AS goals_per_90,
    CASE WHEN minutes > 0 THEN assists * 90.0 / minutes END AS assists_per_90,
    CASE WHEN minutes > 0 THEN shots * 90.0 / minutes END AS shots_per_90,
    CASE WHEN shots > 0 THEN shots_on_target * 100.0 / shots END
        AS shot_accuracy_percentage,
    CASE WHEN shots > 0 THEN goals * 100.0 / shots END
        AS shot_conversion_percentage,
    CASE WHEN penalties_attempted > 0
        THEN penalties_scored * 100.0 / penalties_attempted
    END AS penalty_conversion_percentage,
    CASE WHEN shots_on_target_against > 0
        THEN saves * 100.0 / shots_on_target_against
    END AS save_percentage,
    CASE WHEN appearances > 0 THEN clean_sheets * 100.0 / appearances END
        AS clean_sheet_percentage
FROM player_season_stats;

INSERT INTO schema_version(version) VALUES (5);
