"""
Map table-hockey.eu players to the H2H site's canonical id, the Sportscorpion PlayerID.

1. ithfId matches a RankingID in players_data.csv -> that PlayerID
2. any other ithfId                               -> 9_000_000 + ithfId
3. no ithfId (anonymous players)                  -> 8_000_000 + table-hockey.eu player id

Names are never matched. The mapping is rebuilt every run, so a player who later
shows up on Sportscorpion switches to the real id.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field

import pandas as pd
import requests

PLAYERS_CSV_URL = "https://raw.githubusercontent.com/amundfylling/Scorpion-Scraper-2.0/main/data/players_data.csv"
ITHF_OFFSET = 9_000_000
ANONYMOUS_OFFSET = 8_000_000

log = logging.getLogger(__name__)


def to_int(value) -> int | None:
    """Parse ids stored as 668143, "668143", 668143.0 or "668143.0"; None for blanks."""
    if value is None or value is pd.NA:
        return None
    if isinstance(value, float):
        return None if pd.isna(value) else int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def load_ranking_map(source: str = PLAYERS_CSV_URL) -> dict[int, int]:
    """ITHF id (RankingID) -> Sportscorpion PlayerID. source is a URL or a local path."""
    if source.startswith(("http://", "https://")):
        response = requests.get(source, timeout=60)
        response.raise_for_status()
        frame = pd.read_csv(io.StringIO(response.content.decode("utf-8-sig")), dtype=str)
    else:
        frame = pd.read_csv(source, dtype=str, encoding="utf-8-sig")
    return build_ranking_map(frame)


def build_ranking_map(frame: pd.DataFrame) -> dict[int, int]:
    mapping: dict[int, int] = {}
    duplicates = 0
    for player_id, ranking_id in zip(frame["PlayerID"], frame["RankingID"]):
        ithf, pid = to_int(ranking_id), to_int(player_id)
        if ithf is None or pid is None:
            continue
        if ithf in mapping:
            duplicates += 1
            # Keep the lowest PlayerID so the choice does not depend on row order.
            pid = min(pid, mapping[ithf])
        mapping[ithf] = pid
    if duplicates:
        log.warning("%d RankingIDs appear more than once in players_data.csv", duplicates)
    return mapping


@dataclass
class PlayerMapper:
    ranking_map: dict[int, int]
    # Unique players seen, keyed by ITHF id or ("anon", table-hockey.eu id), with how each was mapped.
    seen: dict[object, str] = field(default_factory=dict)

    def map(self, site_id, ithf_id) -> int:
        ithf = to_int(ithf_id)
        if ithf is not None:
            if ithf in self.ranking_map:
                self.seen[ithf] = "mapped"
                return self.ranking_map[ithf]
            self.seen[ithf] = "synthetic"
            return ITHF_OFFSET + ithf
        self.seen[("anon", int(site_id))] = "anonymous"
        return ANONYMOUS_OFFSET + int(site_id)

    def stats(self) -> dict[str, int]:
        counts = {"mapped": 0, "synthetic": 0, "anonymous": 0}
        for method in self.seen.values():
            counts[method] += 1
        counts["total"] = len(self.seen)
        return counts
