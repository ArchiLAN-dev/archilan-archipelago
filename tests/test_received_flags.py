"""Story 27.14: the AP flags of a received item, whatever shape the bridge or the save gives it.

A recent bridge sends [item, player, location, flags]; an older one three elements; the .apsave holds
NetworkItems with a `flags` field. `items_received` ORs them per item.

Compiled straight from reachable.py: that module imports Archipelago at module level, which only
exists inside the AP container.
"""
from __future__ import annotations

import ast
import os
from collections import namedtuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _entry_flags():
    with open(os.path.join(_REPO_ROOT, "reachable.py"), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_entry_flags"]
    namespace: dict = {}
    exec(compile(ast.Module(body=body, type_ignores=[]), "reachable.py", "exec"), namespace)
    return namespace["_entry_flags"]


def test_a_recent_bridge_sends_the_flags_as_the_fourth_element() -> None:
    assert _entry_flags()([101, 2, 401, 1]) == 1
    assert _entry_flags()((101, 2, 401, 4)) == 4


def test_an_older_bridge_sends_three_elements_and_the_flags_are_zero() -> None:
    assert _entry_flags()([101, 2, 401]) == 0
    assert _entry_flags()([101, 2, 401, None]) == 0


def test_a_save_holds_network_items_with_their_flags() -> None:
    network_item = namedtuple("NetworkItem", ["item", "location", "player", "flags"])
    assert _entry_flags()(network_item(101, 401, 2, 2)) == 2


def test_items_received_carry_the_flags_ored_per_item() -> None:
    with open(os.path.join(_REPO_ROOT, "reachable.py"), encoding="utf-8") as handle:
        source = handle.read()
    assert 'item_flags[name] = item_flags.get(name, 0) | _entry_flags(entry)' in source
    assert '"flags": item_flags.get(name, 0)' in source
