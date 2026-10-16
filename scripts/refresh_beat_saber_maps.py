#!/usr/bin/env python3
"""Refresh the Beat Saber song list the image ships (story 38.17).

The beat_saber apworld (CurrentlyPending/BeatSaberAP) draws the songs of a randomized playlist from
`ranked_maps.json`: BeatLeader's ranked maps since 2020. It downloads that list itself on its first
generation, but a generation container has no network and no `/Archipelago/data` to write to, so the
image carries the list instead (data/beat_saber/ranked_maps.json, copied by the Dockerfile).

This script rebuilds it exactly as the apworld does (same endpoint, filters and fields, same order -
the apworld draws with random.choice, so order is part of the result) and prints what changed, for the
refresh PR. Standard library only: it runs in CI without installing anything.

    python scripts/refresh_beat_saber_maps.py            # rewrite the list, print the summary
    python scripts/refresh_beat_saber_maps.py --check    # print the summary, write nothing
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

BASE_URL = "https://api.beatleader.com/maps"
PAGE_SIZE = 100
# Same filters as the apworld's BeatLeaderRankedMaps.DEFAULT_PARAMS.
DEFAULT_PARAMS = {"type": "ranked", "date_from": 1577872242}
LIST_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "beat_saber", "ranked_maps.json")


def fetch_all_ranked_maps() -> list[dict]:
    """Every page, in BeatLeader's order, with the apworld's 0.2 s pause between pages."""
    all_maps: list[dict] = []
    page = 0
    while True:
        query = urllib.parse.urlencode({"page": page, "count": PAGE_SIZE, **DEFAULT_PARAMS})
        request = urllib.request.Request(f"{BASE_URL}?{query}", headers={"User-Agent": "archilan-archipelago-image"})
        with urllib.request.urlopen(request, timeout=30) as response:
            maps = json.load(response).get("data", [])
        if not maps:
            return all_maps
        all_maps.extend(maps)
        page += 1
        time.sleep(0.2)


def extract_fields(raw_maps: list[dict]) -> list[dict]:
    """The apworld's BeatLeaderRankedMaps.extract_fields, field for field."""
    result = []
    for m in raw_maps:
        difficulties = {}
        for diff in m.get("difficulties", []):
            name = diff.get("difficultyName")
            stars = diff.get("stars")
            if name and stars is not None:
                difficulties[name] = {
                    "stars": stars,
                    "pass_stars": diff.get("passRating"),
                    "acc_stars": diff.get("accRating"),
                    "tech_stars": diff.get("techRating"),
                    "map_type": diff.get("type"),
                }
        raw_id = m.get("id")
        clean_id = raw_id.replace("x", "").replace("X", "") if isinstance(raw_id, str) else None
        result.append({
            "map_name": m.get("name"),
            "level_id": clean_id,
            "hash": m.get("hash"),
            "star_ratings": difficulties,
            "tags": m.get("tags", []),
        })
    return result


def change_summary(before: list[dict], after: list[dict]) -> str:
    """What the refresh PR says: counts, and the maps that left the list (a song a past seed may still use)."""
    old = {m.get("hash"): m for m in before}
    new = {m.get("hash"): m for m in after}
    added = [h for h in new if h not in old]
    removed = [old[h] for h in old if h not in new]
    lines = [f"{len(after)} cartes ({len(added)} ajoutée(s), {len(removed)} retirée(s))."]
    if removed:
        lines.append("Retirées :")
        lines.extend(f"- {m.get('map_name')} ({m.get('hash')})" for m in removed)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh the Beat Saber song list the image ships.")
    parser.add_argument("--check", action="store_true", help="print what would change, write nothing")
    args = parser.parse_args()

    before: list[dict] = []
    if os.path.exists(LIST_PATH):
        with open(LIST_PATH, encoding="utf-8") as handle:
            before = json.load(handle)

    after = extract_fields(fetch_all_ranked_maps())
    if not after:
        print("BeatLeader returned no map: the list is left as it was.", file=sys.stderr)
        return 1

    print(change_summary(before, after))
    if not args.check:
        os.makedirs(os.path.dirname(LIST_PATH), exist_ok=True)
        with open(LIST_PATH, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(after, handle, indent=1, ensure_ascii=False)
            handle.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
