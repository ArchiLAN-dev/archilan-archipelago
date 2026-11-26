"""The tracking counts each received item once.

`CollectionState.collect` runs a full `sweep_for_advancements` whenever an item changes the state. The
world the tracking rebuilds is not filled, so that sweep collects the locked items (Majora's Mask
Recompiled locks a Progressive Shield on "Link's Inventory (Hero's Shield)"), which received_items
already holds once their location is checked. One Progressive Shield read as two, so the logic saw the
Mirror Shield and marked "Ikana Castle King Song" reachable. Checked on reachable.py's source: importing
it loads Archipelago.
"""
from __future__ import annotations

import ast
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TREE = ast.parse(open(os.path.join(_REPO_ROOT, "reachable.py"), encoding="utf-8").read())


def _compute() -> ast.FunctionDef:
    for node in ast.walk(_TREE):
        if isinstance(node, ast.ClassDef) and node.name == "_SlotTracker":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "compute":
                    return item
    raise AssertionError("_SlotTracker.compute not found")


def _collect_calls() -> list[ast.Call]:
    return [
        n for n in ast.walk(_compute())
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "collect"
    ]


def test_received_items_are_collected_without_a_sweep():
    calls = _collect_calls()
    assert calls, "compute no longer collects the received items"
    for call in calls:
        flags = {kw.arg: kw.value for kw in call.keywords}
        value = flags.get("prevent_sweep")
        assert isinstance(value, ast.Constant) and value.value is True, (
            "collect() without prevent_sweep=True sweeps the locked items in: they count twice"
        )


def test_only_the_events_are_swept():
    sweeps = [
        n for n in ast.walk(_compute())
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "sweep_for_advancements"
    ]
    assert sweeps, "the event locations are no longer swept"
    for call in sweeps:
        assert any(kw.arg == "locations" for kw in call.keywords), "a sweep over every location collects locked items"
