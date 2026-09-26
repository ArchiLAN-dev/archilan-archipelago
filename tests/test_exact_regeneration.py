"""Regression tests for the exact regeneration of a multiworld in the reachability pass.

reachable.py used to rebuild one player alone with a fresh seed, so every roll came out different:
Hollow Knight's shop slots and prices, any `random-range` option, any world-internal draw. AP's
generation is deterministic - same yamls, same apworlds, same AP version, same seed, same result -
and the seed is on the first line of the spoiler written next to the multidata. Regenerating the
whole multiworld with it reproduced a real 10-player run exactly (2729 locations, spheres, starting
inventories).

`_read_generation_seed` finds that seed; `_exact_mismatch` is the guard that proves the rebuilt
world is the real one before it is trusted (an AP upgrade or a changed apworld breaks the
reproduction, and the pass then falls back on the single-player rebuild). Both are compiled straight
from reachable.py: that module imports Archipelago at module level, which only exists inside the AP
container.
"""
from __future__ import annotations

import ast
import os
import pathlib
import re
import zipfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WANTED = {"_read_generation_seed", "_exact_mismatch", "_SEED_LINE"}

SPOILER = "﻿Archipelago Version 0.6.7  -  Seed: 3542097186775176328\n\nFilling Algorithm: balanced\n"


def _load() -> dict:
    with open(os.path.join(_REPO_ROOT, "reachable.py"), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    body = [
        node
        for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in _WANTED)
        or (isinstance(node, ast.Assign) and any(getattr(t, "id", None) in _WANTED for t in node.targets))
    ]
    namespace: dict = {"re": re, "zipfile": zipfile, "Path": pathlib.Path}
    exec(compile(ast.Module(body=body, type_ignores=[]), "reachable.py", "exec"), namespace)
    return namespace


def test_the_seed_is_read_from_the_spoiler_next_to_the_multidata(tmp_path):
    (tmp_path / "AP_1653.archipelago").write_bytes(b"x")
    (tmp_path / "AP_1653_Spoiler.txt").write_text(SPOILER, encoding="utf-8")

    assert _load()["_read_generation_seed"](str(tmp_path / "AP_1653.archipelago")) == 3542097186775176328


def test_the_seed_is_read_from_the_spoiler_inside_the_output_zip(tmp_path):
    archive = tmp_path / "AP_1653.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("AP_1653.archipelago", b"x")
        zf.writestr("AP_1653_Spoiler.txt", SPOILER)

    assert _load()["_read_generation_seed"](str(archive)) == 3542097186775176328


def test_no_spoiler_means_no_seed(tmp_path):
    """A race seed, or one imported from elsewhere: nothing to reproduce, the fallback applies."""
    (tmp_path / "AP_1653.archipelago").write_bytes(b"x")

    assert _load()["_read_generation_seed"](str(tmp_path / "AP_1653.archipelago")) is None


ARCH = {
    "locations": {1: {101: (5, 1, 0), 102: (6, 2, 0)}, 2: {201: (7, 1, 0)}},
    "precollected_items": {1: [900], 2: []},
}


def test_a_faithful_regeneration_passes_the_guard():
    mismatch = _load()["_exact_mismatch"]({1: {101, 102}, 2: {201}}, {1: [900], 2: []}, ARCH)

    assert mismatch is None


def test_a_different_set_of_locations_is_caught():
    """What an AP upgrade or a re-uploaded apworld does: the world no longer rolls the same way."""
    mismatch = _load()["_exact_mismatch"]({1: {101, 103}, 2: {201}}, {1: [900], 2: []}, ARCH)

    assert mismatch is not None and "slot 1" in mismatch


def test_a_different_starting_inventory_is_caught():
    mismatch = _load()["_exact_mismatch"]({1: {101, 102}, 2: {201}}, {1: [901], 2: []}, ARCH)

    assert mismatch is not None and "slot 1" in mismatch


def test_a_missing_slot_is_caught():
    mismatch = _load()["_exact_mismatch"]({1: {101, 102}}, {1: [900]}, ARCH)

    assert mismatch is not None and "slot 2" in mismatch
