"""Regression tests for the starting inventory the reachability pass hands to CollectionState.

`push_precollected()` runs during `create_items`, and a world is free to draw its starting items at
random: Sayonara Wild Hearts picks the level you begin with via `world.random.choice`. reachable.py
rebuilds the world with a fresh seed, so the regenerated starting inventory is not the one the
player actually got - three consecutive passes over the same save answered Laser Love, Hate Skulls
and Forest Dub, while the real seed had precollected Heartbreak I. Reachability was wrong in both
directions: a level the player never unlocked showed as playable, and the one they did was hidden.

The multidata records what was really precollected, so `_seed_precollected_items` replaces the
rolled inventory with it. The function is compiled straight from reachable.py: that module imports
Archipelago at module level, which only exists inside the AP container.
"""
from __future__ import annotations

import ast
import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_seed_precollected_items():
    with open(os.path.join(_REPO_ROOT, "reachable.py"), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    func = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_seed_precollected_items"
    )
    namespace: dict = {"sys": sys}
    exec(compile(ast.Module(body=[func], type_ignores=[]), "reachable.py", "exec"), namespace)
    return namespace["_seed_precollected_items"]


class FakeMultiWorld:
    """Stands in for the regenerated MultiWorld: it already rolled a starting inventory of its own."""

    def __init__(self, rolled: list[str], creatable: set[str] | None = None) -> None:
        self.precollected_items = {1: [f"item:{name}" for name in rolled]}
        self._creatable = creatable

    def create_item(self, name: str, player: int) -> str:
        if self._creatable is not None and name not in self._creatable:
            raise KeyError(name)
        return f"item:{name}"


@pytest.fixture(name="seed_precollected_items")
def _fixture():
    return _load_seed_precollected_items()


def test_the_seeds_starting_inventory_replaces_the_rolled_one(seed_precollected_items):
    """The reported bug: the run started on Heartbreak I, the regeneration rolled Laser Love."""
    mw = FakeMultiWorld(rolled=["Laser Love"])

    seed_precollected_items(mw, 1, {"precollected_items": {2: [7000]}}, 2, {7000: "Heartbreak I"})

    assert mw.precollected_items[1] == ["item:Heartbreak I"]


def test_a_seed_without_a_starting_inventory_clears_the_rolled_one(seed_precollected_items):
    """Replace, never merge: what the regeneration rolled is by definition not what was handed out."""
    mw = FakeMultiWorld(rolled=["Laser Love"])

    seed_precollected_items(mw, 1, {"precollected_items": {2: []}}, 2, {7000: "Heartbreak I"})

    assert mw.precollected_items[1] == []


def test_every_precollected_item_of_the_slot_is_kept(seed_precollected_items):
    mw = FakeMultiWorld(rolled=[])
    arch = {"precollected_items": {2: [7000, 7001]}}

    seed_precollected_items(mw, 1, arch, 2, {7000: "Heartbreak I", 7001: "Bow"})

    assert mw.precollected_items[1] == ["item:Heartbreak I", "item:Bow"]


def test_another_slots_inventory_is_ignored(seed_precollected_items):
    mw = FakeMultiWorld(rolled=[])
    arch = {"precollected_items": {2: [7000], 3: [7001]}}

    seed_precollected_items(mw, 1, arch, 2, {7000: "Heartbreak I", 7001: "Someone Else's"})

    assert mw.precollected_items[1] == ["item:Heartbreak I"]


def test_an_id_absent_from_the_datapackage_is_skipped(seed_precollected_items):
    mw = FakeMultiWorld(rolled=[])
    arch = {"precollected_items": {2: [7000, 9999]}}

    seed_precollected_items(mw, 1, arch, 2, {7000: "Heartbreak I"})

    assert mw.precollected_items[1] == ["item:Heartbreak I"]


def test_an_item_the_world_can_no_longer_create_does_not_take_down_the_pass(seed_precollected_items):
    """An apworld updated since the seed was rolled may not know the name any more."""
    mw = FakeMultiWorld(rolled=[], creatable={"Heartbreak I"})
    arch = {"precollected_items": {2: [7000, 7001]}}

    seed_precollected_items(mw, 1, arch, 2, {7000: "Heartbreak I", 7001: "Renamed Item"})

    assert mw.precollected_items[1] == ["item:Heartbreak I"]


def test_a_multidata_without_the_key_leaves_an_empty_inventory(seed_precollected_items):
    mw = FakeMultiWorld(rolled=["Laser Love"])

    seed_precollected_items(mw, 1, {}, 2, {})

    assert mw.precollected_items[1] == []
