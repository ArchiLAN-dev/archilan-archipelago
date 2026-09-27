"""Accessibility not met: a warning, as the official Launcher does (story 38.12).

`MultiWorld.fulfills_accessibility` raises `FillError` under `if __debug__:` and otherwise logs a warning and
returns False; `Main.main` then fails only if the game is unbeatable. The Windows Launcher runs optimized
(`__debug__` false) and generates; our image runs plain Python and failed - Dragon Ball Z Budokai Tenkaichi 2
with its default options "works locally" and not on ArchiLAN. The generator now behaves like the Launcher for
this one check, and says so; every other `assert` of Archipelago stays armed.
"""
from __future__ import annotations

import ast
import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

import generation_setup  # noqa: E402

MISSING = "Could not access required locations for accessibility check. Missing: [Discover: Evil Dragon, Discover: Negative Energy, Discover: Ultimate Dragonball]"


class FakeFillError(Exception):
    pass


def _world(outcome):
    class FakeMultiWorld:
        def fulfills_accessibility(self, state=None):
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    return FakeMultiWorld


def test_accessibility_not_met_becomes_a_warning():
    world_cls = _world(FakeFillError(MISSING))
    warnings = generation_setup.soften_accessibility_check(world_cls, FakeFillError)

    assert world_cls().fulfills_accessibility() is False, "False, as the optimized Launcher returns"
    assert warnings == [{
        "type": "accessibility",
        "message": MISSING,
        "missing": ["Discover: Evil Dragon", "Discover: Negative Energy", "Discover: Ultimate Dragonball"],
    }]


def test_the_placements_dump_is_left_out():
    # Archipelago appends every placement of the game to the message: thousands of characters no admin reads.
    world_cls = _world(FakeFillError(MISSING + "\nAll Placements:\n[(Saiyan Saga - Mission 00, Z-Item: Speed +13)]"))
    warnings = generation_setup.soften_accessibility_check(world_cls, FakeFillError)

    world_cls().fulfills_accessibility()

    assert warnings[0]["message"] == MISSING
    assert len(warnings[0]["missing"]) == 3


def test_a_met_check_is_left_alone():
    world_cls = _world(True)
    warnings = generation_setup.soften_accessibility_check(world_cls, FakeFillError)

    assert world_cls().fulfills_accessibility() is True
    assert warnings == []


def test_any_other_fill_error_still_raises():
    world_cls = _world(FakeFillError("Game appears as unbeatable. Aborting."))
    generation_setup.soften_accessibility_check(world_cls, FakeFillError)

    with pytest.raises(FakeFillError):
        world_cls().fulfills_accessibility()


def test_softening_twice_does_not_wrap_twice():
    world_cls = _world(FakeFillError(MISSING))
    first = generation_setup.soften_accessibility_check(world_cls, FakeFillError)
    second = generation_setup.soften_accessibility_check(world_cls, FakeFillError)

    world_cls().fulfills_accessibility()

    assert second is first, "the same record list: warnings are not lost to a second arming"
    assert len(first) == 1


def test_the_warning_line_carries_the_record():
    line = generation_setup.warning_line({"type": "accessibility", "message": "m", "missing": ["A"]})

    assert line.startswith(generation_setup.WARNING_SENTINEL + " ")
    assert '"missing": ["A"]' in line


def _calls(script: str) -> set[str]:
    tree = ast.parse(open(os.path.join(_REPO_ROOT, script), encoding="utf-8").read())
    return {
        (n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", ""))
        for n in ast.walk(tree) if isinstance(n, ast.Call)
    }


@pytest.mark.parametrize("script", ["generate_multiworld.py", "reachable.py"])
def test_every_generating_script_softens_the_check(script):
    # reachable.py runs Generate again for the exact tracking: without it, a run that generated would lose
    # its reachability on the same check.
    assert "soften_accessibility_check" in _calls(script), f"{script} still fails where the Launcher warns"


def test_the_generator_reports_its_warnings():
    assert "warning_line" in _calls("generate_multiworld.py")
