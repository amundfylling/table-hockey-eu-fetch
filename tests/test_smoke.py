import unittest
from collections import Counter

from table_hockey_eu.players import PlayerMapper
from table_hockey_eu.transform import transform


class SmokeTest(unittest.TestCase):
    def test_minimal(self):
        detail = {"name": "T", "startDate": "2026-01-02T00:00:00", "players": [{"id": 1, "ithfId": 5}, {"id": 2, "ithfId": None}],
                  "stages": [{"id": 10, "name": "Groups", "type": "RoundRobin"}, {"id": 11, "name": "Playoff", "type": "Playoff"}]}
        stages = {10: {"groups": [{"label": "A", "matches": [
            {"id": 100, "roundNumber": 1, "homePlayerId": 1, "homePlayerName": "a", "awayPlayerId": 2, "awayPlayerName": "b", "homeScore": 2, "awayScore": 2, "walkover": "None"},
            {"id": 101, "roundNumber": 1, "homePlayerId": 1, "homePlayerName": "a", "awayPlayerId": None, "isBye": True, "walkover": "None"}]}]},
            11: {"playoffRounds": [{"roundNumber": 1, "bandStartPlace": 1, "bandEndPlace": 2, "matches": [
                {"id": 7, "homePlayerId": 1, "homePlayerName": "a", "awayPlayerId": 2, "awayPlayerName": "b", "walkover": "None",
                 "games": [{"gameNumber": 1, "homeScore": 3, "awayScore": 1, "wentToOvertime": True}, {"id": 0, "gameNumber": 2, "homeScore": None}]}]}]}}
        frame, skipped = transform([({"id": 3, "status": "Completed"}, detail, stages)], PlayerMapper({5: 42}))
        self.assertEqual(len(frame), 2)
        self.assertEqual(frame["Stage"].tolist(), ["Groups", "Playoff 1-2 R1"])
        self.assertEqual(frame["Player2ID"].tolist(), [8_000_002, 8_000_002])
        self.assertEqual(skipped, Counter(carried_forward=0, bye=1, unplayed=1, walkover=0))
