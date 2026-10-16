"""Story 38.17: the Beat Saber song list the image ships, and the script that refreshes it.

The beat_saber apworld draws its songs from `ranked_maps.json`, a cache it downloads from BeatLeader on its
first generation and writes next to Archipelago. In our sealed container it can do neither (no network, and
`/Archipelago/data` does not exist), so the image carries the list. These tests hold the shape the apworld
reads, the order it draws from, and the summary the refresh PR shows.
"""
from __future__ import annotations

import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))

import refresh_beat_saber_maps as refresh  # noqa: E402

_LIST = os.path.join(_REPO_ROOT, "data", "beat_saber", "ranked_maps.json")


def _raw_map(map_id: str, name: str, hash_: str, stars: float = 5.0) -> dict:
    return {
        "id": map_id,
        "name": name,
        "hash": hash_,
        "tags": ["tech"],
        "difficulties": [
            {"difficultyName": "Expert", "stars": stars, "passRating": 4.0, "accRating": 3.0, "techRating": 2.0, "type": 4},
            {"difficultyName": "Easy", "stars": None},
        ],
    }


def test_extract_matches_what_the_apworld_reads():
    entries = refresh.extract_fields([_raw_map("1a2bx", "Song", "abc")])

    assert entries == [
        {
            "map_name": "Song",
            "level_id": "1a2b",
            "hash": "abc",
            "star_ratings": {"Expert": {"stars": 5.0, "pass_stars": 4.0, "acc_stars": 3.0, "tech_stars": 2.0, "map_type": 4}},
            "tags": ["tech"],
        }
    ]


def test_extract_keeps_the_order_beatleader_returns():
    # The apworld draws with random.choice: the same seed picks the same songs only from the same order.
    entries = refresh.extract_fields([_raw_map("2", "B", "h2"), _raw_map("1", "A", "h1")])

    assert [e["hash"] for e in entries] == ["h2", "h1"]


def test_the_summary_names_the_maps_that_left_the_list():
    before = [{"hash": "h1", "map_name": "Kept"}, {"hash": "h2", "map_name": "Gone"}]
    after = [{"hash": "h1", "map_name": "Kept"}, {"hash": "h3", "map_name": "New"}]

    summary = refresh.change_summary(before, after)

    assert "1 ajoutée" in summary
    assert "1 retirée" in summary
    assert "Gone" in summary


def test_the_shipped_list_is_what_the_apworld_expects():
    with open(_LIST, encoding="utf-8") as handle:
        maps = json.load(handle)

    assert isinstance(maps, list) and len(maps) > 100
    for entry in maps[:50]:
        assert set(entry) == {"map_name", "level_id", "hash", "star_ratings", "tags"}
        assert entry["star_ratings"], "the apworld reads a difficulty of every map it draws"


def test_the_dockerfile_puts_the_list_where_the_apworld_looks():
    # beat_saber resolves `/Archipelago/data/ranked_maps.json` from a world unpacked under /tmp.
    with open(os.path.join(_REPO_ROOT, "Dockerfile"), encoding="utf-8") as handle:
        dockerfile = handle.read()

    assert "COPY data/beat_saber/ranked_maps.json /Archipelago/data/ranked_maps.json" in dockerfile
