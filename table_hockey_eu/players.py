"""
Map table-hockey.eu players to the H2H site's canonical id, the Sportscorpion PlayerID.

1. ithfId matches a RankingID in players_data.csv            -> that PlayerID ("mapped")
2. ithfId is in data/player_overrides.csv                     -> that PlayerID ("manual")
3. ithfId that players_data.csv lacks, but the player's name
   is there exactly once (folded, any word order, quoted
   nicknames dropped, same nation when both sides have one)
   under a PlayerID with an empty RankingID                   -> that PlayerID ("name")
4. any other ithfId                                           -> 9_000_000 + ithfId ("synthetic")
5. no ithfId (anonymous players)                              -> 8_000_000 + table-hockey.eu player id

Rule 3 is table-hockey-scrapers' "ithf_name_sportscorpion" rule. Every name decision is logged
(data/name_matches.csv). Guard: a name match is refused when the player would get the same id as an
opponent they play in the same tournament (they are two people); the transform then rebuilds the rows.
The mapping is rebuilt every run, so a player who later shows up on Sportscorpion switches to the real id.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

import pandas as pd
import requests

PLAYERS_CSV_URL = "https://raw.githubusercontent.com/amundfylling/Scorpion-Scraper-2.0/main/data/players_data.csv"
OVERRIDES_PATH = os.path.join("data", "player_overrides.csv")
ITHF_OFFSET = 9_000_000
ANONYMOUS_OFFSET = 8_000_000
METHODS = ("mapped", "manual", "name", "synthetic", "anonymous")

log = logging.getLogger(__name__)

# ITHF / IOC / ISO codes as table-hockey.eu sends them -> the ITHF code.
_CODE_ALIASES = {"LVA": "LAT", "DEU": "GER", "SVN": "SLO", "DNK": "DEN", "CHE": "SUI", "PRT": "POR"}
# Country names (players_data.csv is free text, some in Russian) -> ITHF code. Shared with table-hockey-scrapers.
_COUNTRY_CODES: dict[str, str] = {
    "russia": "RUS", "россия": "RUS", "росиия": "RUS", "latvia": "LAT", "латвия": "LAT", "norway": "NOR",
    "ukraine": "UKR", "kazakhstan": "KAZ", "казакстан": "KAZ", "kazakistan": "KAZ", "estonia": "EST",
    "belarus": "BLR", "sweden": "SWE", "sverige": "SWE", "germany": "GER", "czech republic": "CZE",
    "czechia": "CZE", "slovakia": "SVK", "finland": "FIN", "slovenia": "SLO", "denmark": "DEN",
    "united states of america": "USA", "republic of lithuania": "LTU", "lithuania": "LTU", "switzerland": "SUI",
    "portugal": "POR", "hungary": "HUN", "tajikistan": "TJK", "israel": "ISR", "canada": "CAN", "syria": "SYR",
    "china": "CHN", "turkmenistan": "TKM", "kyrgyzstan": "KGZ", "serbia": "SRB", "france": "FRA", "india": "IND",
    "pakistan": "PAK", "spain": "ESP", "uzbekistan": "UZB", "austria": "AUT", "azerbaijan": "AZE",
    "chile": "CHI", "united kingdom": "GBR", "australia": "AUS", "italy": "ITA", "poland": "POL",
    "netherlands": "NED", "armenia": "ARM", "iran": "IRN", "republic of moldova": "MDA",
}
# ITHF code -> country name, as players_data.csv and table-hockey-scrapers write it.
COUNTRY_NAMES: dict[str, str] = {
    "LAT": "Latvia", "CZE": "Czech Republic", "FIN": "Finland", "SWE": "Sweden", "NOR": "Norway",
    "EST": "Estonia", "RUS": "Russia", "UKR": "Ukraine", "BLR": "Belarus", "KAZ": "Kazakhstan", "GER": "Germany",
    "SVK": "Slovakia", "SLO": "Slovenia", "DEN": "Denmark", "LTU": "Republic of Lithuania", "SUI": "Switzerland",
    "HUN": "Hungary", "AUT": "Austria", "USA": "United States of America", "CAN": "Canada", "POR": "Portugal",
    "POL": "Poland", "FRA": "France",
}
_NO_COUNTRY = {"", "-", "test"}
_EXTRA_FOLD = str.maketrans({"ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D", "ß": "ss", "æ": "ae",
                             "Æ": "AE", "ı": "i"})
# Quoted nicknames: 'Raivis "The Cobra" Miglinieks', 'Jan „Honza“ Novák'.
_NICKNAME = re.compile(r'["“”„«»][^"“”„«»]*["“”„«»]')


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


def fold(text: str) -> str:
    """ASCII-fold, lowercase and collapse punctuation and whitespace: "Kovaļčuks  Andris" -> "kovalcuks andris"."""
    text = unicodedata.normalize("NFKD", str(text).translate(_EXTRA_FOLD))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def name_key(name: str) -> frozenset[str]:
    """Order-independent folded tokens without quoted nicknames: "Liepiņš Edgars" == "Edgars Liepins"."""
    return frozenset(fold(_NICKNAME.sub(" ", str(name))).split())


def nation_code(code: str | None) -> str | None:
    code = (code or "").strip().upper()
    return _CODE_ALIASES.get(code, code) or None


def country_name(code: str | None) -> str | None:
    """Country name for a table-hockey.eu country code ("NOR" -> "Norway"); the code itself when unknown."""
    code = nation_code(code)
    return COUNTRY_NAMES.get(code, code) if code else None


def country_codes(country: str | None) -> frozenset[str] | None:
    """ITHF code(s) for a players_data.csv Country; None when blank. "Sweden / Portugal" -> {SWE, POR}.
    An unknown country becomes {"?<name>"}: it is a nation, but matches no source nation."""
    text = (country or "").strip().lower()
    if text in _NO_COUNTRY:
        return None
    codes = {_COUNTRY_CODES.get(part, f"?{part}") for part in re.split(r"\s*[/,]\s*|\s+-\s+", text)
             if part.strip() not in _NO_COUNTRY}
    return frozenset(codes) or None


@dataclass
class Candidate:
    player_id: int
    name: str
    country: str
    ranking_id: int | None


@dataclass
class Reference:
    """What the mapper needs from players_data.csv."""

    ranking_map: dict[int, int]
    by_name: dict[frozenset, list[Candidate]] = field(default_factory=dict)


def _read_players_csv(source: str) -> pd.DataFrame:
    if source.startswith(("http://", "https://")):
        response = requests.get(source, timeout=60)
        response.raise_for_status()
        return pd.read_csv(io.StringIO(response.content.decode("utf-8-sig")), dtype=str, keep_default_na=False)
    return pd.read_csv(source, dtype=str, encoding="utf-8-sig", keep_default_na=False)


def load_reference(source: str = PLAYERS_CSV_URL) -> Reference:
    """players_data.csv from a URL or a local path."""
    return build_reference(_read_players_csv(source))


def load_ranking_map(source: str = PLAYERS_CSV_URL) -> dict[int, int]:
    """ITHF id (RankingID) -> Sportscorpion PlayerID. source is a URL or a local path."""
    return load_reference(source).ranking_map


def build_ranking_map(frame: pd.DataFrame) -> dict[int, int]:
    return build_reference(frame).ranking_map


def build_reference(frame: pd.DataFrame) -> Reference:
    mapping: dict[int, int] = {}
    by_name: dict[frozenset, list[Candidate]] = defaultdict(list)
    duplicates = 0
    names = frame["Name"] if "Name" in frame else [""] * len(frame)
    countries = frame["Country"] if "Country" in frame else [""] * len(frame)
    for player_id, ranking_id, name, country in zip(frame["PlayerID"], frame["RankingID"], names, countries):
        ithf, pid = to_int(ranking_id), to_int(player_id)
        if pid is None:
            continue
        key = name_key(name or "")
        if key:
            by_name[key].append(Candidate(pid, str(name).strip(), str(country or "").strip(), ithf))
        if ithf is None:
            continue
        if ithf in mapping:
            duplicates += 1
            # Keep the lowest PlayerID so the choice does not depend on row order.
            pid = min(pid, mapping[ithf])
        mapping[ithf] = pid
    if duplicates:
        log.warning("%d RankingIDs appear more than once in players_data.csv", duplicates)
    return Reference(mapping, dict(by_name))


def load_overrides(path: str = OVERRIDES_PATH) -> dict[int, int]:
    """data/player_overrides.csv (ithf_id,player_id,name,reason): confirmed mappings the name rule cannot make."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return {int(row["ithf_id"]): int(row["player_id"]) for row in csv.DictReader(fh)}


NAME_LOG_FIELDS = ["ithf_id", "name", "nation", "result", "player_id", "matched_name", "candidates"]


@dataclass
class PlayerMapper:
    ranking_map: dict[int, int]
    by_name: dict[frozenset, list[Candidate]] = field(default_factory=dict)
    overrides: dict[int, int] = field(default_factory=dict)
    # Unique players seen, keyed by ITHF id or ("anon", table-hockey.eu id), with how each was mapped.
    seen: dict[object, str] = field(default_factory=dict)
    # (ITHF id, PlayerID) name matches refused because the two play each other in a tournament.
    blocked: dict[tuple[int, int], str] = field(default_factory=dict)
    # One entry per (ITHF id, name, nation) name decision.
    name_log: dict[tuple, dict] = field(default_factory=dict)

    @classmethod
    def from_reference(cls, ref: Reference, overrides: dict[int, int] | None = None) -> "PlayerMapper":
        return cls(ref.ranking_map, ref.by_name, dict(overrides or {}))

    def candidates(self, name: str, nation: str | None) -> list[Candidate]:
        """Same folded name; country does not contradict ``nation`` (a row without one stays, so it still counts
        against uniqueness); RankingID empty or this player's (a different RankingID is someone else)."""
        nation = nation_code(nation)
        out = []
        for cand in self.by_name.get(name_key(name), []):
            codes = country_codes(cand.country)
            if nation and codes is not None and nation not in codes:
                continue
            out.append(cand)
        return out

    def match_name(self, ithf: int, name: str, nation: str | None) -> int | None:
        cands = [c for c in self.candidates(name, nation) if c.ranking_id is None or c.ranking_id == ithf]
        ids = sorted({c.player_id for c in cands})
        result = ids[0] if len(ids) == 1 else None
        if result is None:
            status = "ambiguous" if ids else "no_match"
        elif (ithf, result) in self.blocked:
            status, result = f"refused: {self.blocked[(ithf, ids[0])]}", None
        else:
            status = "matched"
        self.name_log[(ithf, name, nation or "")] = {
            "ithf_id": ithf,
            "name": name,
            "nation": nation or "",
            "result": status,
            "player_id": "" if result is None else result,
            "matched_name": cands[0].name if len(ids) == 1 else "",
            "candidates": "" if len(ids) == 1 else "|".join(f"{c.player_id}:{c.name}:{c.country}" for c in cands),
        }
        return result

    def resolve(self, site_id, ithf_id, name: str | None = None, nation: str | None = None) -> tuple[int, str]:
        ithf = to_int(ithf_id)
        if ithf is None:
            return ANONYMOUS_OFFSET + int(site_id), "anonymous"
        if ithf in self.ranking_map:
            return self.ranking_map[ithf], "mapped"
        if ithf in self.overrides:
            return self.overrides[ithf], "manual"
        if name:
            pid = self.match_name(ithf, name, nation)
            if pid is not None:
                return pid, "name"
        return ITHF_OFFSET + ithf, "synthetic"

    def map(self, site_id, ithf_id, name: str | None = None, nation: str | None = None) -> int:
        pid, method = self.resolve(site_id, ithf_id, name, nation)
        ithf = to_int(ithf_id)
        self.seen[ithf if ithf is not None else ("anon", int(site_id))] = method
        return pid

    def method(self, ithf_id) -> str | None:
        return self.seen.get(to_int(ithf_id))

    def block(self, ithf: int, player_id: int, reason: str) -> bool:
        """Refuse the name match ithf -> player_id from now on. True when it was not refused yet."""
        if (ithf, player_id) in self.blocked:
            return False
        self.blocked[(ithf, player_id)] = reason
        log.info("ITHF %d not mapped to Sportscorpion %d by name: %s", ithf, player_id, reason)
        return True

    def reset(self) -> None:
        """Forget what was seen (before a rebuild); keeps the blocks."""
        self.seen = {}
        self.name_log = {}

    def stats(self) -> dict[str, int]:
        counts = {method: 0 for method in METHODS}
        for method in self.seen.values():
            counts[method] += 1
        counts["total"] = len(self.seen)
        return counts

    def write_name_log(self, path: str) -> None:
        rows = sorted(self.name_log.values(), key=lambda r: (r["ithf_id"], r["name"], r["nation"]))
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=NAME_LOG_FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
