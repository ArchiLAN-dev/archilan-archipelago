"""Item ids are only unique within a game: two games of one multiworld may share an id.

Reported on a run with Minecraft and Left 4 Dead: a slot's lists showed items of another game. The tracker
read every name in a single table merging every game's datapackage, so a shared id took the name of
whichever game came last. A name is now read in the game of the slot that receives the item.

Compiled straight from reachable.py: that module imports Archipelago at module level, which only exists
inside the AP container.
"""
from __future__ import annotations

import ast
import os
from collections import Counter
from types import SimpleNamespace

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tracker_class() -> type:
    with open(os.path.join(_REPO_ROOT, "reachable.py"), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    body = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "_SlotTracker"]
    namespace: dict = {"Counter": Counter, "MultiWorld": object}
    exec(compile(ast.Module(body=body, type_ignores=[]), "reachable.py", "exec"), namespace)
    return namespace["_SlotTracker"]


# Slot 1 plays Minecraft, slot 2 Left 4 Dead; both games use id 100.
ARCH = {
    "slot_info": {1: SimpleNamespace(game="Minecraft", name="Steve"), 2: SimpleNamespace(game="Left 4 Dead", name="Zoey")},
    "datapackage": {
        "Minecraft": {"item_name_to_id": {"Diamond Pickaxe": 100}, "location_name_to_id": {"Mine": 10}},
        "Left 4 Dead": {"item_name_to_id": {"Molotov": 100}, "location_name_to_id": {"Saferoom": 20}},
    },
    # location id -> (item id, receiving slot, flags)
    "locations": {
        1: {10: (100, 2, 1)},  # Steve's location holds Zoey's Molotov
        2: {20: (100, 1, 1)},  # Zoey's location holds Steve's Diamond Pickaxe
    },
    "spheres": [],
}


def _world(game_items: dict[int, str]) -> SimpleNamespace:
    return SimpleNamespace(worlds={1: SimpleNamespace(item_id_to_name=game_items), 2: SimpleNamespace(item_id_to_name=game_items)}, get_locations=lambda player: [])


def test_an_item_is_named_in_the_game_of_its_receiver() -> None:
    tracker = _tracker_class()(ARCH, 1, _world({100: "Diamond Pickaxe"}), 1, True)

    assert tracker.item_name(100, 1) == "Diamond Pickaxe"
    assert tracker.item_name(100, 2) == "Molotov", "the Molotov in Steve's world is Zoey's, a Left 4 Dead item"


def test_a_slot_waits_for_items_of_its_own_game_only() -> None:
    steve = _tracker_class()(ARCH, 1, _world({100: "Diamond Pickaxe"}), 1, True)
    zoey = _tracker_class()(ARCH, 2, _world({100: "Molotov"}), 2, True)

    assert steve.expected_counter == Counter({"Diamond Pickaxe": 1})
    assert zoey.expected_counter == Counter({"Molotov": 1})


def test_an_unknown_id_keeps_its_number() -> None:
    tracker = _tracker_class()(ARCH, 1, _world({}), 1, True)

    assert tracker.item_name(999, 2) == "#999"
    assert tracker.item_name(100, 7) == "#100", "a slot missing from slot_info has no game to read the name in"
