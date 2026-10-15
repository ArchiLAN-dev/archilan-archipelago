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


# ─── The file the launcher provides (story 44.2) ──────────────────────────────


def test_the_required_file_arguments_are_taken_out_of_argv():
    argv = ["generate_multiworld.py", "--player_files_path", "/data/yamls",
            "--required_file", "DK3Settings.rom_file=/tmp/required-files/0/dkc3.sfc",
            "--outputpath", "/data/output", "--required_file", "not-a-setting=/tmp/x", "--required_file"]

    files = generation_setup.take_required_file_args(argv)

    assert files == {"DK3Settings.rom_file": "/tmp/required-files/0/dkc3.sfc"}
    assert argv == ["generate_multiworld.py", "--player_files_path", "/data/yamls",
                    "--outputpath", "/data/output", "--required_file"]


def test_the_host_section_is_the_settings_key_of_the_world_declaring_the_group(monkeypatch):
    worlds = _install_world(monkeypatch)
    worlds["Donkey Kong Country 3"].settings_key = "dkc3_options"

    host, unknown = generation_setup.required_file_host_settings(
        {"DK3Settings.rom_file": "/tmp/required-files/0/dkc3.sfc", "OOTSettings.rom_file": "/tmp/oot.z64"}, worlds)

    assert host == {"dkc3_options": {"rom_file": "/tmp/required-files/0/dkc3.sfc"}}
    assert unknown == ["OOTSettings.rom_file"]


def test_applying_the_files_merges_host_yaml_and_drops_the_settings_cache(tmp_path, monkeypatch):
    worlds = _install_world(monkeypatch)
    worlds["Donkey Kong Country 3"].settings_key = "dkc3_options"

    def get_settings():
        return None

    get_settings._cache = object()
    sys.modules["settings"].get_settings = get_settings
    monkeypatch.setitem(sys.modules, "Utils", types.SimpleNamespace(user_path=lambda name: str(tmp_path / name)))
    (tmp_path / "host.yaml").write_text("dkc3_options:\n  release: true\n", encoding="utf-8")

    generation_setup.apply_required_files({"DK3Settings.rom_file": "/tmp/required-files/0/dkc3.sfc"}, worlds)

    import yaml

    data = yaml.safe_load((tmp_path / "host.yaml").read_text(encoding="utf-8"))
    assert data == {"dkc3_options": {"release": True, "rom_file": "/tmp/required-files/0/dkc3.sfc"}}
    assert not hasattr(get_settings, "_cache")


def test_no_file_provided_leaves_host_yaml_alone(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "Utils", types.SimpleNamespace(user_path=lambda name: str(tmp_path / name)))

    generation_setup.apply_required_files({}, {})
    generation_setup.apply_required_files({"Nope.rom_file": "/tmp/x"}, {})

    assert not (tmp_path / "host.yaml").exists()


# ─── The files a world declares, read without generating (story 44.3) ──────────


def _install_declaring_world(monkeypatch):
    fake_settings = types.ModuleType("settings")
    fake_settings.Group = _Group

    class FilePath(_Path):
        required = True
        is_exe = False

    fake_settings.FilePath = FilePath
    monkeypatch.setitem(sys.modules, "settings", fake_settings)

    class RomFile(FilePath):
        description = "DKC3 (US) ROM File"
        md5s = [b"\x12\x0a"]

    class OptionalRom(FilePath):
        required = False

    class Emulator(FilePath):
        is_exe = True

    class BaseSettings(_Group):
        rom_file = RomFile("dkc3.sfc")

    class DK3Settings(BaseSettings):
        rom_start = True
        optional_rom = OptionalRom("other.sfc")
        emulator = Emulator("EmuHawk.exe")
        plain_text = "not a path"

    package = types.ModuleType("worlds.fake_declaring")
    package.DK3Settings = DK3Settings
    monkeypatch.setitem(sys.modules, "worlds.fake_declaring", package)
    return type("DKC3World", (), {"__module__": "worlds.fake_declaring", "game": "Donkey Kong Country 3"}), fake_settings


def test_a_world_declares_its_required_files_inherited_ones_included(monkeypatch):
    world, fake_settings = _install_declaring_world(monkeypatch)

    assert generation_setup.declared_required_files(world, fake_settings) == [{
        "setting": "DK3Settings.rom_file",
        "file": "dkc3.sfc",
        "extension": ".sfc",
        "description": "DKC3 (US) ROM File",
        "md5s": ["120a"],
    }]


def test_a_world_that_cannot_be_read_declares_nothing(monkeypatch):
    world, _ = _install_declaring_world(monkeypatch)

    assert generation_setup.declared_required_files(world, types.ModuleType("settings")) == []
    assert generation_setup.declared_required_files(type("Broken", (), {"__module__": "worlds.none"})) == []


def test_the_scripts_report_the_declared_files():
    """The introspection carries them, and the generator lists them for the release's worlds."""
    from test_generation_setup import _calls

    assert "declared_required_files" in _calls("introspect_options.py")
    assert "declared_required_files" in _calls("generate_multiworld.py")
