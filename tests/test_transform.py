"""Transform tests on tournaments saved from the real API (tests/fixtures/raw/)."""

import os
import tempfile
import unittest

import pandas as pd

from table_hockey_eu.cache import RawCache, finished_tournaments
from table_hockey_eu.players import PlayerMapper, load_ranking_map
from table_hockey_eu.transform import COLUMNS, INT_COLUMNS, transform

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
RAW = os.path.join(FIXTURES, "raw")
BORDSHOCKEY_DTYPES = {col: ("Int64" if col in INT_COLUMNS else "string") for col in COLUMNS}


def _build():
    mapper = PlayerMapper(load_ranking_map(os.path.join(FIXTURES, "players_data.csv")))
    frame, skipped = transform(finished_tournaments(RawCache(RAW)), mapper)
    return frame, skipped, mapper


class TransformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame, cls.skipped, cls.mapper = _build()

    def rows(self, **filters):
        mask = pd.Series(True, index=self.frame.index)
        for col, value in filters.items():
            mask &= self.frame[col] == value
        return self.frame[mask]

    def test_schema_matches_bordshockey(self):
        self.assertEqual(list(self.frame.columns), COLUMNS)
        self.assertEqual({c: str(t) for c, t in self.frame.dtypes.items()}, BORDSHOCKEY_DTYPES)

    def test_row_counts_per_tournament(self):
        counts = self.frame.groupby("SourceTournamentID").size().to_dict()
        # The Planned tournament 34 is listed but never emitted.
        self.assertEqual(counts, {"3": 63, "26": 5, "30": 48, "33": 60})

    def test_skip_counts(self):
        self.assertEqual(dict(self.skipped), {"carried_forward": 12, "bye": 16, "unplayed": 18, "walkover": 4})

    def test_carried_forward_matches_are_dropped(self):
        # Stage 37 (B-mellomspill) opens with six results carried forward from the group stage.
        ids = set(self.rows(SourceStageID="37")["SourceMatchID"])
        self.assertTrue({"6264", "6265", "6266", "6267", "6268", "6269"}.isdisjoint(ids))
        self.assertIn("6270", ids)

    def test_round_robin_draw_is_kept_with_group_label(self):
        row = self.rows(SourceMatchID="6224").iloc[0]
        self.assertEqual(row["Stage"], "Grunnspill B")
        self.assertEqual((row["GoalsPlayer1"], row["GoalsPlayer2"]), (4, 4))
        self.assertEqual(row["Player1"], "Andreas Fjermestad")
        self.assertEqual(row["Player2"], "Jonas Bolstad")
        self.assertEqual((row["Player1ID"], row["Player2ID"]), (1001, 1002))
        self.assertEqual(row["RoundNumber"], 3)
        self.assertTrue(pd.isna(row["PlayoffGameNumber"]))
        self.assertEqual(row["Overtime"], "No")
        self.assertEqual(row["StageType"], "roundrobin")
        self.assertEqual(row["StageSequence"], 1)
        self.assertEqual(row["Date"], "2026-09-30")
        self.assertEqual(row["TournamentName"], "Jærliga 2026/27 #8")
        self.assertEqual(row["TournamentID"], 2_000_000_003)
        self.assertEqual(row["StageID"], 2_000_000_032)
        self.assertEqual(row["TournamentURL"], "https://table-hockey.eu/tournaments/3")
        for col in ("ResultURL", "StageURL", "SourceURL"):
            self.assertEqual(row[col], "https://table-hockey.eu/tournaments/3/stages/32")
        self.assertEqual(row["Source"], "table-hockey.eu")
        self.assertEqual((row["SourceTournamentID"], row["SourceStageID"]), ("3", "32"))

    def test_single_group_stage_has_no_label(self):
        self.assertEqual(set(self.rows(SourceStageID="36")["Stage"]), {"Mellomspill"})

    def test_playoff_rows_are_games_and_skip_unplayed(self):
        series = self.rows(SourceStageID="38")
        self.assertEqual(
            series["SourceMatchID"].tolist(), ["68-1", "68-2", "69-1", "69-2", "73-1", "70-1", "70-2"]
        )
        final_game = self.rows(SourceMatchID="70-2").iloc[0]
        self.assertEqual(final_game["Stage"], "Sluttspill 1-4 R2")
        self.assertEqual((final_game["GoalsPlayer1"], final_game["GoalsPlayer2"]), (3, 4))
        self.assertEqual(final_game["Overtime"], "Yes")
        self.assertEqual(final_game["RoundNumber"], 2)
        self.assertEqual(final_game["PlayoffGameNumber"], 2)
        self.assertEqual(final_game["StageType"], "playoff")
        self.assertEqual(self.rows(SourceMatchID="73-1").iloc[0]["Stage"], "Sluttspill 3-4 R1")

    def test_walkovers_are_skipped(self):
        ids = set(self.frame["SourceMatchID"])
        self.assertNotIn("14948", ids)  # round robin, walkover Away
        self.assertNotIn("14953", ids)  # round robin, walkover Home
        self.assertFalse(any(i.startswith(("614-", "618-")) for i in ids))  # playoff series, walkover Away

    def test_bye_series_is_skipped(self):
        self.assertFalse(any(i.startswith("436-") for i in self.frame["SourceMatchID"]))

    def test_player_ids(self):
        ids = set(self.frame["Player1ID"]) | set(self.frame["Player2ID"])
        self.assertIn(9_000_000 + 660648, ids)  # ITHF id not on Sportscorpion
        self.assertIn(8_000_000 + 7928, ids)  # anonymous, no ITHF id
        self.assertEqual(self.mapper.stats()["mapped"], 2)
        self.assertEqual(self.mapper.stats()["anonymous"], 3)

    def test_sorted_and_reproducible(self):
        dates = self.frame["Date"].tolist()
        self.assertEqual(dates, sorted(dates))
        with tempfile.TemporaryDirectory() as tmp:
            first, second = os.path.join(tmp, "a.parquet"), os.path.join(tmp, "b.parquet")
            self.frame.to_parquet(first, index=False)
            _build()[0].to_parquet(second, index=False)
            with open(first, "rb") as a, open(second, "rb") as b:
                self.assertEqual(a.read(), b.read())
            back = pd.read_parquet(first)
            self.assertEqual({c: str(t) for c, t in back.dtypes.items()}, BORDSHOCKEY_DTYPES)


if __name__ == "__main__":
    unittest.main()
