"""Story 17.28: one reachability daemon per session instead of one per slot.

The exact path rebuilds the whole multiworld, every player's world, so one rebuild serves every
slot: `_SessionTrackers` loads it once and builds each slot's tracker on first use; `_serve` routes
each request to its slot. A slot that cannot be built keeps its error and the others keep working.

Benched in the archipelago image on a 6-player run (2026-10-07): the same results slot by slot, 2.2 s
for one daemon against 11.3 s for one process per slot, each process peaking at ~200 MB.

Compiled straight from reachable.py: that module imports Archipelago at module level, which only
exists inside the AP container.
"""
from __future__ import annotations

import ast
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WANTED = {"_SessionTrackers", "_serve"}


class _Tracker:
    def __init__(self, arch: dict, slot: int, mw: object, player_id: int, exact: bool) -> None:
        self.slot, self.mw, self.exact = slot, mw, exact

    def compute(self, checked_ids: set, received_items: list) -> dict:
        return {"slot": self.slot, "checked": sorted(checked_ids), "exact": self.exact, "counts": {}}


def _load(exact: object | None, emitted: list, built: list) -> dict:
    with open(os.path.join(_REPO_ROOT, "reachable.py"), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    body = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in _WANTED]
    loads: list[str] = []

    def load_exact(arch_path: str, yamls_dir: str, arch: dict) -> object | None:
        loads.append(arch_path)
        return exact

    def build_multiworld(game: str, name: str, yaml_path: str, slot_data: dict) -> tuple[object, int]:
        built.append(name)
        if name == "broken":
            raise RuntimeError("generate_early raised")
        return object(), 1

    arch = {"slot_info": {1: SimpleNamespace(game="A", name="alice"), 2: SimpleNamespace(game="B", name="broken")}}
    namespace: dict = {
        "MultiWorld": object,
        "_SlotTracker": _Tracker,
        "load_archipelago": lambda path: arch,
        "_load_exact_multiworld": load_exact,
        "build_multiworld": build_multiworld,
        "Path": Path,
        "_emit": emitted.append,
        "json": json,
        "sys": sys,
        "loads": loads,
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), "reachable.py", "exec"), namespace)
    return namespace


def test_the_exact_world_is_rebuilt_once_for_every_slot() -> None:
    world = object()
    ns = _load(world, [], [])
    session = ns["_SessionTrackers"]("run.archipelago", "/yamls")

    first, second = session.tracker(1), session.tracker(2)

    assert first.mw is world and second.mw is world, "every slot shares the one rebuilt world"
    assert first.exact and second.exact
    assert ns["loads"] == ["run.archipelago"], "rebuilt once"
    assert session.tracker(1) is first, "a slot's tracker is built once"


def test_without_the_exact_world_each_slot_is_rebuilt_alone_and_a_failure_stays_its_own(tmp_path: Path) -> None:
    (tmp_path / "alice.yaml").write_text("name: alice")
    (tmp_path / "broken.yaml").write_text("name: broken")
    built: list = []
    ns = _load(None, [], built)
    session = ns["_SessionTrackers"]("run.archipelago", str(tmp_path))

    assert not session.tracker(1).exact
    with pytest.raises(ValueError, match="reachability generation failed for B"):
        session.tracker(2)
    with pytest.raises(ValueError, match="generate_early raised"):
        session.tracker(2)
    assert built == ["alice", "broken"], "a slot that failed is not rebuilt at every request"
    with pytest.raises(ValueError, match="slot 9 not found"):
        session.tracker(9)


def test_each_request_is_answered_for_its_slot_and_an_error_never_stops_the_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    emitted: list = []
    ns = _load(object(), emitted, [])
    session = ns["_SessionTrackers"]("run.archipelago", "/yamls")
    requests = [
        {"slot": 2, "checked_locations": [5, 3]},
        {"checked_locations": []},
        {"slot": 9},
        {"slot": 1},
    ]
    monkeypatch.setattr("sys.stdin", io.StringIO("".join(json.dumps(r) + "\n" for r in requests)))

    ns["_serve"](session, default_slot=None)

    assert emitted[0] == {"ready": True}
    assert emitted[1] == {"slot": 2, "checked": [3, 5], "exact": True, "counts": {}}
    assert emitted[2] == {"error": "request without a slot"}
    assert emitted[3] == {"error": "slot 9 not found"}
    assert emitted[4]["slot"] == 1, "the daemon goes on after an error"


def test_a_per_slot_daemon_still_answers_requests_without_a_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    emitted: list = []
    ns = _load(object(), emitted, [])
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"checked_locations": [1]}) + "\n"))

    ns["_serve"](ns["_SessionTrackers"]("run.archipelago", "/yamls"), default_slot=1)

    assert emitted[1]["slot"] == 1
