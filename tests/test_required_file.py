"""A world that needs a file at generation: no file dialog, and the file named in the failure record (story 44.1).

Archipelago opens a file browser when a required host.yaml `Path` is missing - impossible in our container,
where tkinter is a stub, so the generation broke further on for an unrelated reason. With `no_gui` it raises
`FileNotFoundError: <file> does not exist, but <Group>.<key> is required`, which the record now carries.
"""
from __future__ import annotations

import os
import sys
import types

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

import generation_setup  # noqa: E402

MESSAGE = "Donkey Kong Country 3 - Dixie Kong's Double Trouble! (USA) (En,Fr).sfc does not exist, but DK3Settings.rom_file is required"


class _Group:
    pass


class _Path(str):
    description = None
    md5s: list = []


def _install_world(monkeypatch):
    """A fake `settings` module and a DKC3-like world whose package declares its settings group."""
    fake_settings = types.ModuleType("settings")
    fake_settings.Group = _Group
    monkeypatch.setitem(sys.modules, "settings", fake_settings)

    class RomFile(_Path):
        description = "DKC3 (US) ROM File"
        md5s = ["120abf304f0c40fe059f6a192ed4f947", b"\x01\x02"]

    class DK3Settings(_Group):
        rom_file = RomFile("Donkey Kong Country 3 - Dixie Kong's Double Trouble! (USA) (En,Fr).sfc")

    package = types.ModuleType("worlds.fake_dkc3")
    package.DK3Settings = DK3Settings
    monkeypatch.setitem(sys.modules, "worlds.fake_dkc3", package)

    world = type("DKC3World", (), {"__module__": "worlds.fake_dkc3", "game": "Donkey Kong Country 3"})
    return {"Donkey Kong Country 3": world}


def test_the_record_names_the_file_with_the_worlds_own_description_and_md5s(monkeypatch):
    worlds = _install_world(monkeypatch)

    record = generation_setup.required_file_record(FileNotFoundError(MESSAGE), worlds)

    assert record == {
        "setting": "DK3Settings.rom_file",
        "file": "Donkey Kong Country 3 - Dixie Kong's Double Trouble! (USA) (En,Fr).sfc",
        "extension": ".sfc",
        "description": "DKC3 (US) ROM File",
        "md5s": ["120abf304f0c40fe059f6a192ed4f947", "0102"],
    }


def test_a_setting_no_loaded_world_declares_still_gives_the_file(monkeypatch):
    _install_world(monkeypatch)

    record = generation_setup.required_file_record(FileNotFoundError(MESSAGE), {})

    assert record == {
        "setting": "DK3Settings.rom_file",
        "file": "Donkey Kong Country 3 - Dixie Kong's Double Trouble! (USA) (En,Fr).sfc",
        "extension": ".sfc",
    }


def test_any_other_failure_has_no_required_file(monkeypatch):
    worlds = _install_world(monkeypatch)

    assert generation_setup.required_file_record(FileNotFoundError("[Errno 2] No such file: 'x.yaml'"), worlds) is None
    assert generation_setup.required_file_record(ValueError(MESSAGE), worlds) is None


def test_file_dialogs_are_disabled():
    fake_settings = types.ModuleType("settings")
    fake_settings.no_gui = False

    generation_setup.disable_file_dialogs(fake_settings)

    assert fake_settings.no_gui is True
