"""Size and progression share of each slot of a generated multiworld (story 56.1).

Archipelago's fill pools the locations of every world and puts each item on the first one it can reach, with no
weighting by world, so a world ends up holding the other players' progression roughly in proportion to its share
of the locations. These three numbers per slot let the site show the world that will hold everyone else back:

- ``locations``: how many locations the world has;
- ``ownProgression``: progression items that belong to the slot, wherever they were placed;
- ``hostedProgression``: progression items of the other slots placed in this world.

Read from an already decoded multidata (the dict ``MultiServer`` loads), so the same code serves an output being
inspected (read_multidata.py, through ``Utils.restricted_loads``) and a generation that has just written one
(generate_multiworld.py). No import from Archipelago: the multidata is plain tuples and ints once decoded.
"""
from __future__ import annotations

from typing import Any

#: ``ItemClassification.progression`` in the flags of a multidata location (``NetworkItem.flags``).
PROGRESSION_FLAG = 0b001

#: ``SlotType.player``: spectators (the Bridge observer) and item-link groups have no world of their own.
PLAYER_SLOT = 1


def _slot_type(info: Any) -> int:
    value = getattr(info, "type", PLAYER_SLOT)
    try:
        return int(value)
    except (TypeError, ValueError):
        return PLAYER_SLOT


def measure(multidata: dict) -> list[dict]:
    """One record per player slot, by slot number: name, game and the three counts."""
    slot_info = multidata.get("slot_info") or {}
    locations = multidata.get("locations") or {}

    players = {
        int(number): info
        for number, info in slot_info.items()
        if _slot_type(info) == PLAYER_SLOT
    }
    sizes = {number: 0 for number in players}
    own = {number: 0 for number in players}
    hosted = {number: 0 for number in players}

    for world, placed in locations.items():
        world = int(world)
        if world not in sizes or not isinstance(placed, dict):
            continue
        sizes[world] = len(placed)
        for entry in placed.values():
            try:
                _item, owner, flags = entry[0], int(entry[1]), int(entry[2])
            except (TypeError, ValueError, IndexError):
                continue
            if not flags & PROGRESSION_FLAG:
                continue
            if owner in own:
                own[owner] += 1
            if owner != world:
                hosted[world] += 1

    return [
        {
            "slot": number,
            "name": str(getattr(players[number], "name", "")),
            "game": str(getattr(players[number], "game", "")),
            "locations": sizes[number],
            "ownProgression": own[number],
            "hostedProgression": hosted[number],
        }
        for number in sorted(players)
    ]
