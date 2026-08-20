"""Regression tests for the apsave `received_items` slot map (issue: doubled item counts).

An Archipelago ``.apsave`` stores ``received_items`` under 3-element keys
``(team, slot, remote_items)``. ``MultiServer.send_items_to`` appends every item a slot
receives to the ``remote_items=True`` list and only the items coming from *other* players to
the ``False`` one, so ``True`` is a superset of ``False``.

The slot map used to concatenate both lists "so the total count is correct". It was not:
every item a slot received was counted twice. In the reachability pass that inflated every
count-based rule (``state.has(item, player, n)``) - on The Wind Waker a single
``Progressive Bow`` read as two, which unlocks fire and ice arrows and reported six
locations as accessible that the player could not reach (Ice Ring Isle cave/frozen chests,
Fire Mountain cave, Needle Rock Isle cave, Southern Fairy Island great fairy).

Both copies of the helper are covered: ``read_save.py`` (the path the bridge actually uses,
via an ephemeral AP container) and ``reachable.py`` (its ``--apsave`` fallback).
"""
from __future__ import annotations

import ast
import importlib.util
import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_read_save_slot_map():
    """Import read_save.py by path and return its _save_slot_map."""
    spec = importlib.util.spec_from_file_location(
        "read_save_under_test", os.path.join(_REPO_ROOT, "read_save.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module._save_slot_map


def _load_reachable_slot_map():
    """Extract _slot_map from reachable.py without importing the module.

    reachable.py imports Archipelago at module level, which only exists inside the AP
    container, so the function is compiled on its own from the source tree.
    """
    with open(os.path.join(_REPO_ROOT, "reachable.py"), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    func = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_slot_map"
    )
    namespace: dict = {}
    exec(compile(ast.Module(body=[func], type_ignores=[]), "reachable.py", "exec"), namespace)
    return namespace["_slot_map"]


@pytest.fixture(params=["read_save", "reachable"])
def slot_map(request):
    if request.param == "read_save":
        return _load_read_save_slot_map()
    return _load_reachable_slot_map()


def test_received_items_are_not_doubled(slot_map):
    """The two remote_items lists describe the same slot: keep one, never concatenate."""
    items = [(101, 0, 1), (102, 0, 2), (103, 0, 3)]
    mapping = {(0, 6, False): list(items), (0, 6, True): list(items)}

    assert slot_map(mapping)[6] == items


def test_remote_true_list_wins_over_the_partial_one(slot_map):
    """`True` holds every item, `False` only those sent by other players."""
    from_others = [(101, 0, 1)]
    everything = [(101, 0, 1), (200, 6, 42)]

    assert slot_map({(0, 6, False): from_others, (0, 6, True): everything})[6] == everything
    # Key order in the pickled save is not guaranteed.
    assert slot_map({(0, 6, True): everything, (0, 6, False): from_others})[6] == everything


def test_falls_back_to_the_partial_list_when_true_is_missing(slot_map):
    from_others = [(101, 0, 1)]

    assert slot_map({(0, 6, False): from_others})[6] == from_others


def test_other_teams_are_ignored(slot_map):
    mapping = {(0, 6, True): [(101, 0, 1)], (1, 6, True): [(999, 0, 9)]}

    assert slot_map(mapping) == {6: [(101, 0, 1)]}


def test_two_element_keys_still_map_to_their_slot(slot_map):
    """location_checks / hints / client_game_state use (team, slot) keys."""
    mapping = {(0, 6): {10, 11}, (0, 7): {20}, (1, 6): {99}}

    assert slot_map(mapping) == {6: {10, 11}, 7: {20}}


def test_plain_int_keys_pass_through(slot_map):
    assert slot_map({6: 30, 7: 0}) == {6: 30, 7: 0}
