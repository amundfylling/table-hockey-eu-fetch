"""
Raw JSON cache under data/raw/, committed to the repository.

    data/raw/tournaments_all.json       the latest /api/tournaments/all listing
    data/raw/tournaments/{id}.json      /api/tournaments/{id}
    data/raw/stages/{id}.json           /api/stages/{id}
    data/raw/index.json                 status of each tournament when it was cached

A tournament is in index.json only once its detail and every stage were saved,
so an interrupted run fetches it again next time.
"""

from __future__ import annotations

import json
import logging
import os

from .api import ApiClient, NotFound

RAW_DIR = os.path.join("data", "raw")
FINISHED = ("Completed", "Archived")

log = logging.getLogger(__name__)


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def _read_json(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class RawCache:
    def __init__(self, root: str = RAW_DIR) -> None:
        self.root = root

    def _path(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)

    def tournament_path(self, tournament_id) -> str:
        return self._path("tournaments", f"{tournament_id}.json")

    def stage_path(self, stage_id) -> str:
        return self._path("stages", f"{stage_id}.json")

    def read_index(self) -> dict[str, str]:
        path = self._path("index.json")
        return _read_json(path) if os.path.exists(path) else {}

    def write_index(self, index: dict[str, str]) -> None:
        _write_json(self._path("index.json"), dict(sorted(index.items(), key=lambda kv: int(kv[0]))))

    def read_listing(self) -> list[dict]:
        path = self._path("tournaments_all.json")
        return _read_json(path) if os.path.exists(path) else []

    def write_listing(self, listing: list[dict]) -> None:
        _write_json(self._path("tournaments_all.json"), sorted(listing, key=lambda t: t["id"]))

    def read_tournament(self, tournament_id) -> dict:
        return _read_json(self.tournament_path(tournament_id))

    def read_stage(self, stage_id) -> dict:
        return _read_json(self.stage_path(stage_id))

    def write_tournament(self, tournament_id, data: dict) -> None:
        _write_json(self.tournament_path(tournament_id), data)

    def write_stage(self, stage_id, data: dict) -> None:
        _write_json(self.stage_path(stage_id), data)


def tournaments_to_fetch(listing: list[dict], index: dict[str, str]) -> list[dict]:
    """Finished tournaments that are not cached yet, or were cached under another status."""
    return [
        t for t in listing if t.get("status") in FINISHED and index.get(str(t["id"])) != t.get("status")
    ]


def crawl(client: ApiClient, cache: RawCache) -> dict:
    """Refresh the cache. Returns a few counts for the run summary."""
    listing = client.tournaments()
    cache.write_listing(listing)
    index = cache.read_index()
    todo = tournaments_to_fetch(listing, index)
    planned = sum(1 for t in listing if t.get("status") not in FINISHED)
    log.info(
        "%d tournaments listed (%d not finished yet), %d to fetch", len(listing), planned, len(todo)
    )

    fetched, failed = 0, 0
    for summary in todo:
        tid = summary["id"]
        try:
            detail = client.tournament(tid)
            stages = {stage["id"]: client.stage(stage["id"]) for stage in detail.get("stages") or []}
        except NotFound as exc:
            log.warning("Tournament %s skipped: %s", tid, exc)
            failed += 1
            continue
        cache.write_tournament(tid, detail)
        for stage_id, stage in stages.items():
            cache.write_stage(stage_id, stage)
        index[str(tid)] = summary["status"]
        cache.write_index(index)
        fetched += 1
        log.info("Cached tournament %s %r (%d stages)", tid, summary.get("name"), len(stages))

    return {"listed": len(listing), "fetched": fetched, "failed": failed, "requests": client.request_count}


def finished_tournaments(cache: RawCache) -> list[tuple[dict, dict, dict[int, dict]]]:
    """(listing entry, detail, stages by id) for every cached finished tournament in the listing."""
    index = cache.read_index()
    out = []
    for summary in cache.read_listing():
        if summary.get("status") not in FINISHED or str(summary["id"]) not in index:
            continue
        detail = cache.read_tournament(summary["id"])
        stages = {s["id"]: cache.read_stage(s["id"]) for s in detail.get("stages") or []}
        out.append((summary, detail, stages))
    return out
