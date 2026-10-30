"""
Crawl table-hockey.eu and write data/table_hockey_eu_results.parquet.

    python -m table_hockey_eu               crawl, then transform the cache
    python -m table_hockey_eu --offline     transform the cache only
"""

from __future__ import annotations

import argparse
import logging
import os

import pandas as pd

from .api import ApiClient
from .cache import RAW_DIR, RawCache, crawl, finished_tournaments
from .players import PLAYERS_CSV_URL, PlayerMapper, load_ranking_map
from .transform import transform

RESULTS_PATH = os.path.join("data", "table_hockey_eu_results.parquet")

log = logging.getLogger("table_hockey_eu")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offline", action="store_true", help="skip the crawl and only transform the cache")
    parser.add_argument("--raw-dir", default=RAW_DIR)
    parser.add_argument("--output", default=RESULTS_PATH)
    parser.add_argument("--players-csv", default=PLAYERS_CSV_URL, help="URL or path of players_data.csv")
    parser.add_argument("--min-interval", type=float, default=1.0, help="seconds between API requests")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cache = RawCache(args.raw_dir)
    if not args.offline:
        summary = crawl(ApiClient(min_interval=args.min_interval), cache)
        log.info(
            "Crawl: %(listed)d listed, %(fetched)d fetched, %(failed)d failed, %(requests)d requests", summary
        )

    ranking_map = load_ranking_map(args.players_csv)
    log.info("Loaded %d Sportscorpion players with an ITHF id", len(ranking_map))
    mapper = PlayerMapper(ranking_map)
    tournaments = finished_tournaments(cache)
    frame, skipped = transform(tournaments, mapper)

    # Leave an unchanged file alone, so a new pyarrow version alone never makes a commit.
    if os.path.exists(args.output) and pd.read_parquet(args.output).equals(frame):
        log.info("%s unchanged", args.output)
    else:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        frame.to_parquet(args.output, index=False, compression="zstd")

    print(f"\nWrote {len(frame)} rows from {frame['TournamentID'].nunique()} tournaments to {args.output}")
    print("\nRows per tournament:")
    per = frame.groupby(["Date", "SourceTournamentID", "TournamentName"], sort=True).size()
    for (date, tid, name), count in per.items():
        print(f"  {date}  {tid:>5}  {count:>5}  {name}")
    empty = [s["id"] for s, _, _ in tournaments if str(s["id"]) not in set(frame["SourceTournamentID"])]
    if empty:
        print(f"  (no rows: tournaments {empty})")
    print("\nSkipped: " + ", ".join(f"{reason} {count}" for reason, count in skipped.items()))
    stats = mapper.stats()
    print(
        f"Players: {stats['total']} unique, {stats['mapped']} mapped to Sportscorpion, "
        f"{stats['synthetic']} synthetic (9M + ITHF id), {stats['anonymous']} anonymous (8M + site id)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
