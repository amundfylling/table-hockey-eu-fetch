import json
import os
import shutil
import tempfile
import unittest

from table_hockey_eu.cache import RawCache, crawl, finished_tournaments, tournaments_to_fetch

RAW = os.path.join(os.path.dirname(__file__), "fixtures", "raw")


def _load(*parts):
    with open(os.path.join(RAW, *parts), encoding="utf-8") as fh:
        return json.load(fh)


class FakeClient:
    """Serves the saved fixtures and records what was requested."""

    def __init__(self, listing):
        self.listing = listing
        self.calls = []
        self.request_count = 0

    def _hit(self, call):
        self.calls.append(call)
        self.request_count += 1

    def tournaments(self):
        self._hit("all")
        return self.listing

    def tournament(self, tid):
        self._hit(f"t{tid}")
        return _load("tournaments", f"{tid}.json")

    def stage(self, sid):
        self._hit(f"s{sid}")
        return _load("stages", f"{sid}.json")


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache = RawCache(self.tmp)
        self.listing = _load("tournaments_all.json")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_tournaments_to_fetch(self):
        listing = [
            {"id": 1, "status": "Completed"},
            {"id": 2, "status": "Archived"},
            {"id": 3, "status": "Planned"},
            {"id": 4, "status": "Completed"},
        ]
        index = {"1": "Completed", "2": "Completed"}
        self.assertEqual([t["id"] for t in tournaments_to_fetch(listing, index)], [2, 4])

    def test_crawl_fetches_finished_once_and_never_planned(self):
        client = FakeClient(self.listing)
        summary = crawl(client, self.cache)
        self.assertEqual(summary["fetched"], 4)
        self.assertNotIn("t34", client.calls)  # Planned
        self.assertIn("s38", client.calls)
        self.assertEqual(self.cache.read_index(), {"3": "Completed", "26": "Completed", "30": "Completed", "33": "Completed"})

        again = FakeClient(self.listing)
        crawl(again, self.cache)
        self.assertEqual(again.calls, ["all"])

        changed = [dict(t, status="Archived") if t["id"] == 26 else t for t in self.listing]
        third = FakeClient(changed)
        crawl(third, self.cache)
        self.assertEqual(third.calls, ["all", "t26", "s130", "s132"])
        self.assertEqual(self.cache.read_index()["26"], "Archived")

    def test_finished_tournaments_skips_planned_and_uncached(self):
        ids = [summary["id"] for summary, _, _ in finished_tournaments(RawCache(RAW))]
        self.assertEqual(ids, [3, 26, 30, 33])


if __name__ == "__main__":
    unittest.main()
