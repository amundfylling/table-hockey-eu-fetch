import os
import unittest

import pandas as pd

from table_hockey_eu.players import PlayerMapper, build_ranking_map, load_ranking_map, to_int

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


class PlayerTests(unittest.TestCase):
    def test_to_int(self):
        self.assertEqual(to_int("668143.0"), 668143)
        self.assertEqual(to_int(668143.0), 668143)
        self.assertEqual(to_int("668143"), 668143)
        self.assertIsNone(to_int(""))
        self.assertIsNone(to_int(None))
        self.assertIsNone(to_int(float("nan")))

    def test_ranking_map_from_csv_with_bom_and_duplicates(self):
        mapping = load_ranking_map(os.path.join(FIXTURES, "players_data.csv"))
        # 671118 is listed twice; the lower PlayerID wins.
        self.assertEqual(mapping, {660870: 1001, 671118: 1002})

    def test_mapping_rules(self):
        mapper = PlayerMapper(build_ranking_map(pd.DataFrame({"PlayerID": ["5"], "RankingID": ["100.0"]})))
        self.assertEqual(mapper.map(1, 100), 5)
        self.assertEqual(mapper.map(2, "200"), 9_000_200)
        self.assertEqual(mapper.map(3, None), 8_000_003)
        self.assertEqual(mapper.map(1, 100), 5)
        self.assertEqual(mapper.stats(), {"mapped": 1, "synthetic": 1, "anonymous": 1, "total": 3})


if __name__ == "__main__":
    unittest.main()
