# table-hockey.eu fetch

Fetches table hockey match results from [table-hockey.eu](https://table-hockey.eu) and writes
`data/table_hockey_eu_results.parquet`, in the same schema as
[bordshockey.net-scraper](https://github.com/amundfylling/bordshockey.net-scraper)'s
`data/bordshockey_results.parquet`, so the
[table-hockey-h2h site](https://github.com/amundfylling/table-hockey-h2h-cloudflare) can read it the same way:

```
https://raw.githubusercontent.com/amundfylling/table-hockey-eu-fetch/refs/heads/main/data/table_hockey_eu_results.parquet
```

A GitHub Action (`.github/workflows/scrape_nightly.yaml`) runs every night at 03:30 UTC and commits any changed data.

## How it works

table-hockey.eu is an Angular app backed by a public JSON API, so nothing is scraped from HTML.
Only anonymous read endpoints are used (`robots.txt` allows `/api/*`):

| Endpoint | Used for |
|---|---|
| `GET /api/tournaments/all` | the list of public tournaments and their status |
| `GET /api/tournaments/{id}` | players (with ITHF ids) and stages in display order |
| `GET /api/stages/{id}` | round-robin groups and matches, or playoff rounds, series and games |

Each run:

1. Fetches `/api/tournaments/all`.
2. Fetches the detail and every stage of each `Completed`/`Archived` tournament that is not cached yet, or was
   cached under another status. `Planned` tournaments are checked again next run and never emitted, since they can
   hold partial results.
3. Downloads Sportscorpion's [`players_data.csv`](https://github.com/amundfylling/Scorpion-Scraper-2.0/blob/main/data/players_data.csv)
   and maps players to ids.
4. Transforms the whole cache into the Parquet file and prints sanity checks.

Requests are at most one per second, with timeouts, retries with backoff and the User-Agent
`table-hockey-h2h-scraper/1.0 (+https://github.com/amundfylling/table-hockey-eu-fetch)`.
A full crawl is about 100 requests; a normal nightly run is 1–10.

### Raw cache

Responses are saved as JSON under `data/raw/` and committed, so finished tournaments are never fetched again:

```
data/raw/tournaments_all.json     latest tournament listing
data/raw/tournaments/{id}.json    tournament detail
data/raw/stages/{id}.json         stage detail
data/raw/index.json               status of each tournament when it was cached
```

A tournament enters `index.json` only after its detail and every stage were saved. To force a refetch, delete its
entry from `index.json`.

### Transform rules

- One row per round-robin match and one row per **playoff game** (not per series).
- Skipped: carried-forward round-robin matches (`isCarriedForward`, copies of earlier-stage results), byes
  (`isBye` or a missing player), unplayed matches or games (no score, e.g. games past the clinch), and walkovers
  (`walkover` other than `"None"`, on a round-robin match or a playoff series).
- Round-robin draws are kept.
- `Date` is the tournament's `startDate` (matches have no timestamps of their own).
- Rows are sorted by date, tournament, stage sequence, round, stage, match and game, so the file only changes when
  the data does.

### Player ids

The H2H site's canonical id is the Sportscorpion `PlayerID`. Names are never matched.

1. The player's ITHF id matches a `RankingID` in `players_data.csv`: that `PlayerID`.
2. Any other ITHF id: `9_000_000 + ithfId`.
3. No ITHF id (anonymous players): `8_000_000 + table-hockey.eu player id`.

The mapping is rebuilt every run, so players who later appear on Sportscorpion switch to their real id.

### Output schema

Integers are pandas `Int64`, text is `string`.

| Column | Value |
|---|---|
| `StageID` | `2_000_000_000 + stage id` (bordshockey ids go up to ~1e9) |
| `Player1`, `Player2` | home / away player name |
| `Player1ID`, `Player2ID` | mapped ids (above) |
| `GoalsPlayer1`, `GoalsPlayer2` | match score (round robin) or game score (playoff) |
| `Overtime` | `Yes` / `No` |
| `Stage` | stage name; `{name} {group}` for round robins with several groups (e.g. `Groups A`); `{name} {band} R{round}` for playoffs (e.g. `Playoff 1-16 R1`) |
| `RoundNumber` | round number |
| `PlayoffGameNumber` | game number in the series, empty for round robin |
| `Date` | `YYYY-MM-DD` |
| `TournamentName` | tournament name |
| `TournamentID` | `2_000_000_000 + tournament id` |
| `StageSequence` | 1-based position of the stage in the tournament |
| `StageType` | `roundrobin` / `playoff` |
| `TournamentURL` | `https://table-hockey.eu/tournaments/{id}` |
| `ResultURL`, `StageURL`, `SourceURL` | `https://table-hockey.eu/tournaments/{id}/stages/{stageId}` |
| `Source` | `table-hockey.eu` |
| `SourceTournamentID`, `SourceStageID` | raw ids |
| `SourceMatchID` | round robin: match id; playoff: `{seriesId}-{gameNumber}` |

## Usage

```bash
pip install -r requirements.txt
python -m table_hockey_eu             # crawl, then write the Parquet file
python -m table_hockey_eu --offline   # rebuild the Parquet file from data/raw/ only
python -m table_hockey_eu --players-csv path/to/players_data.csv
```

Tests use small fixtures saved from the real API (`tests/fixtures/`):

```bash
python -m unittest discover -s tests -v
```

## Files

- `table_hockey_eu/api.py`: rate-limited API client with retries.
- `table_hockey_eu/cache.py`: the raw JSON cache and the crawl.
- `table_hockey_eu/players.py`: Sportscorpion id mapping.
- `table_hockey_eu/transform.py`: cached JSON to rows in the output schema.
- `table_hockey_eu/__main__.py`: command line entry point and sanity checks.
