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

import re

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import generation_setup  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WANTED = {
    "_SessionTrackers", "_serve", "RequiredFileUnavailable", "_error_payload",
    "_slot_yaml", "_NO_WORLD_MARKER", "_YAML_NAME_LINE", "build_multiworld",
}


class _Tracker:
    def __init__(self, arch: dict, slot: int, mw: object, player_id: int, exact: bool) -> None:
        self.slot, self.mw, self.exact = slot, mw, exact

    def compute(self, checked_ids: set, received_items: list) -> dict:
        return {"slot": self.slot, "checked": sorted(checked_ids), "exact": self.exact, "counts": {}}


def _load(exact: object | None, emitted: list, built: list, diverging: dict | None = None) -> dict:
    with open(os.path.join(_REPO_ROOT, "reachable.py"), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    body = [
        node for node in tree.body
        if (isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in _WANTED and node.name != "build_multiworld")
        or (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in _WANTED for t in node.targets))
    ]
    loads: list[str] = []
    yaml_files: list[str] = []

    def load_exact(arch_path: str, yamls_dir: str, arch: dict) -> tuple[object, dict] | None:
        loads.append(arch_path)
        return None if exact is None else (exact, diverging or {})

    def build_multiworld(game: str, name: str, yaml_path: str, slot_data: dict) -> tuple[object, int]:
        built.append(name)
        yaml_files.append(Path(yaml_path).name)
        if name == "broken":
            raise RuntimeError("generate_early raised")
        if name == "rom":
            raise FileNotFoundError("lttp.sfc does not exist, but ALTTPSettings.rom_file is required")
        if name == "phil":
            raise RuntimeError("Encountered 1 error(s) in player files.\nException: No world found to handle game Pokemon Red.")
        return object(), 1

    arch = {"slot_info": {1: SimpleNamespace(game="A", name="alice"), 2: SimpleNamespace(game="B", name="broken"), 3: SimpleNamespace(game="A Link to the Past", name="rom"), 4: SimpleNamespace(game="Pokemon Red", name="phil"), 5: SimpleNamespace(game="Terraria", name="3nvieux")}}
    namespace: dict = {
        "MultiWorld": object,
        "_SlotTracker": _Tracker,
        "load_archipelago": lambda path: arch,
        "_load_exact_multiworld": load_exact,
        "build_multiworld": build_multiworld,
        "Path": Path,
        "re": re,
        "_emit": emitted.append,
        "json": json,
        "sys": sys,
        "loads": loads,
        "yaml_files": yaml_files,
        "required_file_record": generation_setup.required_file_record,
        "AutoWorld": SimpleNamespace(AutoWorldRegister=SimpleNamespace(world_types={})),
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


def test_a_slot_the_rebuild_does_not_reproduce_falls_back_alone(tmp_path: Path) -> None:
    """Story 17.31: one world rolling differently no longer sends every slot to the fallback."""
    (tmp_path / "alice.yaml").write_text("name: alice")
    world = object()
    built: list = []
    ns = _load(world, [], built, diverging={1: "starting inventory differs"})
    session = ns["_SessionTrackers"]("run.archipelago", str(tmp_path))

    alone, real = session.tracker(1), session.tracker(2)

    assert not alone.exact and alone.mw is not world
    assert real.exact and real.mw is world
    assert built == ["alice"], "only the diverging slot is rebuilt alone"


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


def test_a_world_reading_its_file_is_named_and_stays_its_own(tmp_path: Path) -> None:
    """Story 44.4: the slot whose world reads its ROM to build its logic says so; the others are tracked."""
    for name in ("alice", "broken", "rom"):
        (tmp_path / f"{name}.yaml").write_text("game: x", encoding="utf-8")
    emitted: list = []
    ns = _load(None, emitted, [])
    session = ns["_SessionTrackers"]("run.archipelago", str(tmp_path))

    with pytest.raises(ns["RequiredFileUnavailable"], match="il a besoin du fichier « lttp.sfc »"):
        session.tracker(3)
    assert not session.tracker(1).exact

    payload = ns["_error_payload"](ns["RequiredFileUnavailable"]({"setting": "ALTTPSettings.rom_file", "file": "lttp.sfc", "extension": ".sfc"}))
    assert payload["requiredFile"]["setting"] == "ALTTPSettings.rom_file"
    assert "qu'ArchiLAN ne conserve pas" in payload["error"]
    assert ns["_error_payload"](ValueError("boom")) == {"error": "boom"}


def test_a_slot_finds_its_yaml_by_player_name_and_never_takes_another_one(tmp_path: Path) -> None:
    """Story 17.36: slot 5 (3nvieux) used to fall on the folder's first file, the Pokemon Red one."""
    (tmp_path / "phil_trrnt_PR.yaml").write_text("name: phil\ngame: 'Pokemon Red'\n", encoding="utf-8")
    (tmp_path / "registration-42.yaml").write_text("# Terraria\nname: '3nvieux'\ngame: Terraria\n", encoding="utf-8")
    ns = _load(None, [], [])
    session = ns["_SessionTrackers"]("run.archipelago", str(tmp_path))

    assert not session.tracker(5).exact
    assert ns["yaml_files"] == ["registration-42.yaml"]

    (tmp_path / "registration-42.yaml").unlink()
    fresh = ns["_SessionTrackers"]("run.archipelago", str(tmp_path))
    with pytest.raises(ValueError, match="no yaml found for slot 5"):
        fresh.tracker(5)


def test_a_world_missing_from_the_container_is_said_and_stays_its_own(tmp_path: Path) -> None:
    """Story 17.36: the 110-slot game of 2026-11-13, one Pokemon Red slot took every slot down."""
    (tmp_path / "phil.yaml").write_text("name: phil\ngame: 'Pokemon Red'\n", encoding="utf-8")
    (tmp_path / "alice.yaml").write_text("name: alice\n", encoding="utf-8")
    ns = _load(None, [], [])
    session = ns["_SessionTrackers"]("run.archipelago", str(tmp_path))

    with pytest.raises(ValueError, match="le monde « Pokemon Red » n'est pas installé"):
        session.tracker(4)
    assert not session.tracker(1).exact, "the other slots are still tracked"


def test_the_fallback_rolls_only_the_slot_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Story 17.36: Generate reads its whole folder, so the fallback hands it a folder with one file."""
    (tmp_path / "phil.yaml").write_text("name: phil\ngame: 'Pokemon Red'\n", encoding="utf-8")
    (tmp_path / "alice.yaml").write_text("name: alice\ngame: A\n", encoding="utf-8")
    with open(os.path.join(_REPO_ROOT, "reachable.py"), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "build_multiworld"]
    seen: list = []

    class _Stop(Exception):
        pass

    def fake_main(args: object) -> tuple:
        seen.append(sorted(os.listdir(getattr(args, "player_files_path"))))
        raise _Stop()

    fake_generate = SimpleNamespace(main=fake_main, mystery_argparse=lambda: SimpleNamespace())
    monkeypatch.setitem(sys.modules, "Generate", fake_generate)
    import shutil
    import tempfile
    namespace: dict = {"sys": sys, "tempfile": tempfile, "shutil": shutil, "Path": Path, "MultiWorld": object}
    exec(compile(ast.Module(body=body, type_ignores=[]), "reachable.py", "exec"), namespace)

    with pytest.raises(_Stop):
        namespace["build_multiworld"]("A", "alice", str(tmp_path / "alice.yaml"), {})
    assert seen == [["alice.yaml"]]
