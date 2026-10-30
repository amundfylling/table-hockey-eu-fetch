"""
Turn cached API responses into rows with the bordshockey results schema.

One row per round-robin match and one per playoff game. Skipped: carried-forward
round-robin matches (copies of earlier-stage results), byes, unplayed matches or
games, and walkovers. Skips are counted per round-robin match, per playoff series
for byes and walkovers, and per playoff game for unplayed games.
"""

from __future__ import annotations

from collections import Counter

import pandas as pd

from .players import PlayerMapper, to_int

SITE_URL = "https://table-hockey.eu"
SOURCE = "table-hockey.eu"
# bordshockey ids reach about 1e9, so synthetic ids start above that.
ID_OFFSET = 2_000_000_000

COLUMNS = [
    "StageID",
    "Player1",
    "Player1ID",
    "Player2",
    "Player2ID",
    "GoalsPlayer1",
    "GoalsPlayer2",
    "Overtime",
    "Stage",
    "RoundNumber",
    "PlayoffGameNumber",
    "Date",
    "TournamentName",
    "TournamentID",
    "StageSequence",
    "StageType",
    "TournamentURL",
    "ResultURL",
    "StageURL",
    "SourceURL",
    "Source",
    "SourceTournamentID",
    "SourceStageID",
    "SourceMatchID",
]
INT_COLUMNS = [
    "StageID",
    "Player1ID",
    "Player2ID",
    "GoalsPlayer1",
    "GoalsPlayer2",
    "RoundNumber",
    "PlayoffGameNumber",
    "TournamentID",
    "StageSequence",
]
SKIP_REASONS = ("carried_forward", "bye", "unplayed", "walkover")


def _walkover(value) -> bool:
    return value not in (None, "", "None")


def _overtime(value) -> str:
    return "Yes" if value else "No"


def _is_bye(match: dict) -> bool:
    return bool(match.get("isBye")) or match.get("homePlayerId") is None or match.get("awayPlayerId") is None


def _ithf_lookup(detail: dict, stages: dict[int, dict]) -> dict[int, object]:
    """table-hockey.eu player id -> ITHF id (or None) from the tournament and its groups."""
    lookup: dict[int, object] = {}
    for stage in stages.values():
        for group in stage.get("groups") or []:
            for player in group.get("players") or []:
                if player.get("playerId") is not None:
                    lookup[player["playerId"]] = player.get("playerIthfId")
    for player in detail.get("players") or []:
        if player.get("id") is not None:
            lookup[player["id"]] = player.get("ithfId")
    return lookup


def tournament_rows(
    summary: dict, detail: dict, stages: dict[int, dict], mapper: PlayerMapper, skipped: Counter
) -> list[dict]:
    tid = summary["id"]
    ithf = _ithf_lookup(detail, stages)
    for site_id, ithf_id in ithf.items():
        mapper.map(site_id, ithf_id)

    def player_id(site_id) -> int:
        return mapper.map(site_id, ithf.get(site_id))

    start = detail.get("startDate") or summary.get("startDate") or ""
    tournament_url = f"{SITE_URL}/tournaments/{tid}"
    base = {
        "Date": str(start)[:10],
        "TournamentName": detail.get("name") or summary.get("name"),
        "TournamentID": ID_OFFSET + tid,
        "TournamentURL": tournament_url,
        "Source": SOURCE,
        "SourceTournamentID": str(tid),
    }

    rows: list[dict] = []
    for sequence, stage_ref in enumerate(detail.get("stages") or [], start=1):
        stage_id = stage_ref["id"]
        stage = stages[stage_id]
        stage_name = stage_ref.get("name") or stage.get("name") or ""
        stage_type = stage_ref.get("type") or stage.get("type")
        stage_url = f"{tournament_url}/stages/{stage_id}"
        stage_base = {
            **base,
            "StageID": ID_OFFSET + stage_id,
            "StageSequence": sequence,
            "ResultURL": stage_url,
            "StageURL": stage_url,
            "SourceURL": stage_url,
            "SourceStageID": str(stage_id),
        }

        def row(match: dict, home: int, away: int, extra: dict) -> dict:
            return {
                **stage_base,
                "Player1": match.get("homePlayerName"),
                "Player1ID": player_id(match["homePlayerId"]),
                "Player2": match.get("awayPlayerName"),
                "Player2ID": player_id(match["awayPlayerId"]),
                "GoalsPlayer1": home,
                "GoalsPlayer2": away,
                **extra,
            }

        if stage_type == "RoundRobin":
            groups = stage.get("groups") or []
            for group in groups:
                label = f"{stage_name} {group.get('label')}" if len(groups) > 1 else stage_name
                for match in group.get("matches") or []:
                    if match.get("isCarriedForward"):
                        skipped["carried_forward"] += 1
                    elif _is_bye(match):
                        skipped["bye"] += 1
                    elif _walkover(match.get("walkover")):
                        skipped["walkover"] += 1
                    elif match.get("homeScore") is None or match.get("awayScore") is None:
                        skipped["unplayed"] += 1
                    else:
                        rows.append(
                            row(
                                match,
                                match["homeScore"],
                                match["awayScore"],
                                {
                                    "Overtime": _overtime(match.get("wentToOvertime")),
                                    "Stage": label,
                                    "RoundNumber": match.get("roundNumber"),
                                    "PlayoffGameNumber": None,
                                    "StageType": "roundrobin",
                                    "SourceMatchID": str(match["id"]),
                                    "_match": match["id"],
                                    "_game": 0,
                                },
                            )
                        )
        elif stage_type == "Playoff":
            for playoff_round in stage.get("playoffRounds") or []:
                number = playoff_round.get("roundNumber")
                band_start, band_end = playoff_round.get("bandStartPlace"), playoff_round.get("bandEndPlace")
                band = f" {band_start}-{band_end}" if band_start is not None and band_end is not None else ""
                label = f"{stage_name}{band} R{number}"
                for series in playoff_round.get("matches") or []:
                    games = series.get("games") or []
                    if _is_bye(series):
                        skipped["bye"] += 1
                        continue
                    if _walkover(series.get("walkover")):
                        skipped["walkover"] += 1
                        continue
                    for game in games:
                        if game.get("homeScore") is None or game.get("awayScore") is None:
                            skipped["unplayed"] += 1
                            continue
                        rows.append(
                            row(
                                series,
                                game["homeScore"],
                                game["awayScore"],
                                {
                                    "Overtime": _overtime(game.get("wentToOvertime")),
                                    "Stage": label,
                                    "RoundNumber": number,
                                    "PlayoffGameNumber": game.get("gameNumber"),
                                    "StageType": "playoff",
                                    "SourceMatchID": f"{series['id']}-{game.get('gameNumber')}",
                                    "_match": series["id"],
                                    "_game": game.get("gameNumber") or 0,
                                },
                            )
                        )
        else:
            raise ValueError(f"Stage {stage_id} of tournament {tid} has unknown type {stage_type!r}")
    return rows


def build_frame(rows: list[dict]) -> pd.DataFrame:
    """Typed, deterministically sorted DataFrame with exactly the output COLUMNS."""
    frame = pd.DataFrame(rows, columns=[*COLUMNS, "_match", "_game"])
    frame = frame.sort_values(
        ["Date", "TournamentID", "StageSequence", "RoundNumber", "Stage", "_match", "_game"],
        kind="mergesort",
        na_position="last",
    )
    frame = frame[COLUMNS].reset_index(drop=True)
    for col in COLUMNS:
        if col in INT_COLUMNS:
            frame[col] = pd.to_numeric(frame[col].map(to_int)).astype("Int64")
        else:
            frame[col] = frame[col].astype("string")
    return frame


def transform(tournaments, mapper: PlayerMapper) -> tuple[pd.DataFrame, Counter]:
    """tournaments: iterable of (listing entry, detail, stages by id)."""
    skipped: Counter = Counter({reason: 0 for reason in SKIP_REASONS})
    rows: list[dict] = []
    for summary, detail, stages in tournaments:
        rows.extend(tournament_rows(summary, detail, stages, mapper, skipped))
    return build_frame(rows), skipped
