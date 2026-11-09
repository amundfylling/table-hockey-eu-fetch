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
from .players import OVERRIDES_PATH, PLAYERS_CSV_URL, PlayerMapper, load_overrides, load_reference
from .transform import transform

RESULTS_PATH = os.path.join("data", "table_hockey_eu_results.parquet")
NAME_LOG_PATH = os.path.join("data", "name_matches.csv")
ID_CHANGES_PATH = os.path.join("data", "id_changes.csv")

log = logging.getLogger("table_hockey_eu")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offline", action="store_true", help="skip the crawl and only transform the cache")
    parser.add_argument("--raw-dir", default=RAW_DIR)
    parser.add_argument("--output", default=RESULTS_PATH)
    parser.add_argument("--players-csv", default=PLAYERS_CSV_URL, help="URL or path of players_data.csv")
    parser.add_argument("--overrides", default=OVERRIDES_PATH, help="confirmed ITHF id -> PlayerID mappings")
    parser.add_argument("--name-log", default=NAME_LOG_PATH, help="where to write every name-match decision")
    parser.add_argument("--id-changes", default=ID_CHANGES_PATH, help="old_id,new_id log appended to on id changes")
    parser.add_argument("--min-interval", type=float, default=1.0, help="seconds between API requests")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cache = RawCache(args.raw_dir)
    if not args.offline:
        summary = crawl(ApiClient(min_interval=args.min_interval), cache)
        log.info(
            "Crawl: %(listed)d listed, %(fetched)d fetched, %(failed)d failed, %(requests)d requests", summary
        )

    reference = load_reference(args.players_csv)
    log.info("Loaded %d Sportscorpion players with an ITHF id", len(reference.ranking_map))
    mapper = PlayerMapper.from_reference(reference, load_overrides(args.overrides))
    tournaments = finished_tournaments(cache)
    frame, skipped = transform(tournaments, mapper)
    mapper.write_name_log(args.name_log)

    previous = pd.read_parquet(args.output) if os.path.exists(args.output) else None
    changes = id_changes(previous, frame) if previous is not None else []
    if changes:
        append_id_changes(args.id_changes, changes)

    # Leave an unchanged file alone, so a new pyarrow version alone never makes a commit.
    if previous is not None and previous.equals(frame):
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
    cleared = skipped.pop("overtime_flag_cleared", 0)
    print("\nSkipped: " + ", ".join(f"{reason} {count}" for reason, count in skipped.items()))
    print(f"Overtime flags cleared (margin other than one goal): {cleared}")
    stats = mapper.stats()
    print(
        f"Players: {stats['total']} unique, {stats['mapped']} mapped to Sportscorpion by ITHF id, "
        f"{stats['name']} by name, {stats['manual']} by override, "
        f"{stats['synthetic']} synthetic (9M + ITHF id), {stats['anonymous']} anonymous (8M + site id)"
    )
    for entry in sorted(mapper.name_log.values(), key=lambda e: (e["result"] != "matched", e["name"])):
        if entry["result"] in ("matched",) or entry["result"].startswith("refused") or entry["result"] == "ambiguous":
            target = entry["player_id"] or entry["candidates"]
            print(f"  name {entry['result']}: ITHF {entry['ithf_id']} {entry['name']} ({entry['nation']}) -> {target}")
    print(f"\nPlayer ids changed this run: {len(changes)}")
    for old, new, sides in changes[:20]:
        print(f"  {old} -> {new}  ({sides} game sides)")
    return 0


def id_changes(old: pd.DataFrame, new: pd.DataFrame) -> list[tuple[int, int, int]]:
    """(old_id, new_id, game sides) for ids that moved between two outputs, matched on the game.
    Only clean renames: an old id that now splits over several new ids is logged and left out."""
    key = ["SourceStageID", "SourceMatchID"]
    pairs = []
    for side in ("Player1", "Player2"):
        merged = old[[*key, f"{side}ID"]].merge(new[[*key, f"{side}ID"]], on=key, suffixes=("_old", "_new"))
        pairs.append(merged[[f"{side}ID_old", f"{side}ID_new"]].set_axis(["old", "new"], axis=1))
    both = pd.concat(pairs).dropna()
    moved = both[both["old"] != both["new"]]
    out = []
    for old_id, group in moved.groupby("old"):
        targets = both[both["old"] == old_id]["new"].unique()
        if len(targets) != 1:
            log.warning("id %s splits into %s; not written to id_changes", old_id, sorted(map(int, targets)))
            continue
        out.append((int(old_id), int(targets[0]), len(group)))
    return sorted(out)


def append_id_changes(path: str, changes: list[tuple[int, int, int]]) -> None:
    """Append old_id,new_id rows (the H2H site uses them to follow renamed players)."""
    exists = os.path.exists(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as fh:
        if not exists:
            fh.write("old_id,new_id\n")
        for old_id, new_id, _ in changes:
            fh.write(f"{old_id},{new_id}\n")


if __name__ == "__main__":
    raise SystemExit(main())
