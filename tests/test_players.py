import os
import unittest

import pandas as pd

import csv
import tempfile

from table_hockey_eu.players import (
    PlayerMapper,
    build_ranking_map,
    build_reference,
    country_name,
    load_overrides,
    load_ranking_map,
    name_key,
    to_int,
)

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
        self.assertEqual(
            mapper.stats(), {"mapped": 1, "manual": 0, "name": 0, "synthetic": 1, "anonymous": 1, "total": 3}
        )


def _mapper(rows, overrides=None):
    frame = pd.DataFrame(rows, columns=["PlayerID", "Name", "RankingID", "Country"])
    return PlayerMapper.from_reference(build_reference(frame), overrides)


class NameMatchTests(unittest.TestCase):
    """ITHF ids Sportscorpion lacks fall back to a unique Sportscorpion name ("ithf_name_sportscorpion")."""

    def test_name_key_folds_accents_order_and_nicknames(self):
        self.assertEqual(name_key("Andris Kovaļčuks"), name_key("kovalcuks  ANDRIS"))
        self.assertEqual(name_key('Raivis "The Cobra" Miglinieks'), name_key("Miglinieks Raivis"))
        self.assertEqual(name_key("Tollak Nesse Håland"), name_key("Tollak Nesse Haland"))

    def test_unique_name_with_empty_ranking_id(self):
        mapper = _mapper([["15538", "Andris Kovalcuks", "", "Latvia"], ["11392", "Kanutte Holmin Brunvoll", "", ""]])
        self.assertEqual(mapper.map(1, 676787, "Andris Kovaļčuks", "LAT"), 15538)
        # No country on the Sportscorpion side: the name alone decides.
        self.assertEqual(mapper.map(2, 672709, "Holmin Brunvoll Kanutte", "NOR"), 11392)
        self.assertEqual(mapper.stats()["name"], 2)
        log = mapper.name_log[(676787, "Andris Kovaļčuks", "LAT")]
        self.assertEqual((log["result"], log["player_id"], log["matched_name"]), ("matched", 15538, "Andris Kovalcuks"))

    def test_other_nation_is_not_matched(self):
        mapper = _mapper([["500", "Jan Novak", "", "Czech Republic"]])
        self.assertEqual(mapper.map(1, 123, "Jan Novak", "NOR"), 9_000_123)
        self.assertEqual(mapper.name_log[(123, "Jan Novak", "NOR")]["result"], "no_match")

    def test_ambiguous_name_is_not_matched(self):
        mapper = _mapper([["15529", "Edgars Liepins", "", "Latvia"], ["15217", "Edgars Liepiņš", "", "Latvia"]])
        self.assertEqual(mapper.map(1, 676383, "Edgars Liepins", "LAT"), 9_676_383)
        entry = mapper.name_log[(676383, "Edgars Liepins", "LAT")]
        self.assertEqual(entry["result"], "ambiguous")
        self.assertIn("15529", entry["candidates"])
        self.assertIn("15217", entry["candidates"])

    def test_candidate_with_another_ranking_id_is_someone_else(self):
        mapper = _mapper([["700", "Ola Nordmann", "111", "Norway"]])
        self.assertEqual(mapper.map(1, 222, "Ola Nordmann", "NOR"), 9_000_222)

    def test_override_wins_over_the_name_rule(self):
        rows = [["15529", "Edgars Liepins", "", "Latvia"], ["15217", "Edgars Liepiņš", "", "Latvia"]]
        mapper = _mapper(rows, {676383: 15529})
        self.assertEqual(mapper.map(1, 676383, "Edgars Liepins", "LAT"), 15529)
        self.assertEqual(mapper.stats()["manual"], 1)

    def test_ranking_id_wins_over_the_name(self):
        mapper = _mapper([["5", "Ola Nordmann", "100", "Norway"], ["6", "Ola Nordmann", "", "Norway"]])
        self.assertEqual(mapper.map(1, 100, "Ola Nordmann", "NOR"), 5)

    def test_blocked_match_falls_back_to_synthetic(self):
        mapper = _mapper([["15435", "Laura Revheim", "", "Norway"]])
        self.assertEqual(mapper.map(1, 676763, "Laura Revheim", "NOR"), 15435)
        self.assertTrue(mapper.block(676763, 15435, "plays Laura Revheim in tournament 1"))
        self.assertFalse(mapper.block(676763, 15435, "again"))
        mapper.reset()
        self.assertEqual(mapper.map(1, 676763, "Laura Revheim", "NOR"), 9_676_763)
        self.assertTrue(mapper.name_log[(676763, "Laura Revheim", "NOR")]["result"].startswith("refused"))

    def test_name_log_file(self):
        mapper = _mapper([["15435", "Laura Revheim", "", "Norway"]])
        mapper.map(1, 676763, "Laura Revheim", "NOR")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "name_matches.csv")
            mapper.write_name_log(path)
            with open(path, encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
        self.assertEqual(rows[0]["player_id"], "15435")
        self.assertEqual(rows[0]["result"], "matched")

    def test_overrides_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "o.csv")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('ithf_id,player_id,name,reason\n676383,15529,Edgars Liepins,"a, b"\n')
            self.assertEqual(load_overrides(path), {676383: 15529})
            self.assertEqual(load_overrides(os.path.join(tmp, "missing.csv")), {})

    def test_country_name(self):
        self.assertEqual(country_name("NOR"), "Norway")
        self.assertEqual(country_name("LVA"), "Latvia")
        self.assertEqual(country_name("XYZ"), "XYZ")
        self.assertIsNone(country_name(None))


if __name__ == "__main__":
    unittest.main()
