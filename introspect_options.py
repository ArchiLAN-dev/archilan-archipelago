#!/usr/bin/env python3
"""
Introspect Archipelago apworld option types + location names and emit JSON to stdout.

Output: {"options": {"option_key": {"type": "range|choice|toggle|text|weights",
                                    "defaultWeights": {key: int}}},
         "locations": ["Location Name", ...]}

"defaultWeights" is only present for "weights" (OptionDict) options.
"locations" is the STATIC location list (the World class's location_name_to_id keys) -
options-dependent locations are not reflected, so consumers treat it as a hint.
"""
import argparse
import atexit
import importlib
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import traceback
import types
import zipfile

# Kivy: suppress its argument parser and env-var hooks.
os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_NO_ENV_CONFIG", "1")

ARCH_SRC = "/app/ArchipelagoSrc"

# ─── Specific pre-stubs ───────────────────────────────────────────────────────

_mu = types.ModuleType("ModuleUpdate")
_mu.update = lambda *a, **kw: None  # type: ignore[attr-defined]
sys.modules["ModuleUpdate"] = _mu

_winapi_stub = types.ModuleType("_winapi")
_winapi_stub.__getattr__ = lambda name: 0  # type: ignore[method-assign]
sys.modules["_winapi"] = _winapi_stub

import json as _json
_orjson = types.ModuleType("orjson")
_orjson.loads = _json.loads  # type: ignore[attr-defined]
_orjson.dumps = lambda obj, **kw: _json.dumps(obj, default=str).encode()  # type: ignore[attr-defined]
sys.modules["orjson"] = _orjson

# tkinter / _tkinter: GUI toolkit not available in headless containers (the extension ships
# without its libtk8.6.so). Mirrors generate_multiworld.py, so a world whose client UI
# imports tkinter at module level introspects here exactly as it generates.
_tk_stub = types.ModuleType("tkinter")
_tk_stub.__getattr__ = lambda _n: _tk_stub  # type: ignore[attr-defined]
for _tk_name in ("tkinter", "_tkinter", "tkinter.ttk", "tkinter.font",
                 "tkinter.messagebox", "tkinter.filedialog", "tkinter.colorchooser",
                 "tkinter.simpledialog", "tkinter.constants"):
    sys.modules.setdefault(_tk_name, _tk_stub)

try:
    import pkg_resources  # noqa: F401
except ImportError:
    from pip._vendor import pkg_resources as _pr  # type: ignore[no-redef]
    sys.modules["pkg_resources"] = _pr

# ─── Worlds stub + source tree ────────────────────────────────────────────────

_worlds_stub = types.ModuleType("worlds")
_worlds_stub.__path__ = [f"{ARCH_SRC}/worlds"]
_worlds_stub.__package__ = "worlds"
# Point at the real worlds/__init__.py so apworlds that resolve files relative to
# `worlds.__file__` work (e.g. yugiohgx: os.path.dirname(worlds.__file__)/_bizhawk.apworld).
_worlds_stub.__file__ = f"{ARCH_SRC}/worlds/__init__.py"
sys.modules["worlds"] = _worlds_stub
sys.path.insert(0, ARCH_SRC)

parser = argparse.ArgumentParser()
parser.add_argument("--world_directory", required=True)
args = parser.parse_args()

from worlds.AutoWorld import AutoWorldRegister, World  # noqa: E402
from Utils import local_path, user_path  # noqa: E402

_worlds_stub.AutoWorldRegister = AutoWorldRegister
_worlds_stub.World = World
_worlds_stub.local_folder = f"{ARCH_SRC}/worlds"
_user_folder = user_path("worlds") if user_path() != local_path() else user_path("custom_worlds")
try:
    os.makedirs(_user_folder, exist_ok=True)
except OSError:
    _user_folder = None
_worlds_stub.user_folder = _user_folder
_worlds_stub.failed_world_loads = []

# ─── World imports: honest first, stub only what is truly missing ────────────
# See apworld_import.py: a world that ships its own fallback for a missing dependency
# takes it, and only a module proven missing gets stubbed.
from apworld_import import import_world  # noqa: E402

# ── Load custom apworld ───────────────────────────────────────────────────────

def _sanitize_pkg_name(name: str) -> str:
    """A Python-importable name for an apworld folder that is not one (e.g. "Twilight Princess")."""
    sanitized = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    return sanitized


def _detect_pkg(entries: list[str]) -> str | None:
    """The apworld's package folder: the one holding __init__.py, else the first entry's root."""
    for entry in entries:
        parts = entry.replace("\\", "/").split("/")
        if len(parts) == 2 and parts[1] == "__init__.py" and parts[0]:
            return parts[0]
    for entry in entries:
        root = entry.replace("\\", "/").split("/")[0]
        if root:
            return root
    return None


_loaded_pkg_names: list[str] = []

for _apw in sorted(pathlib.Path(args.world_directory).glob("*.apworld")):
    try:
        with zipfile.ZipFile(str(_apw)) as zf:
            entries = zf.namelist()
        if not entries:
            continue
        raw_pkg_name = _detect_pkg(entries)
    except Exception as exc:
        print(f"Warning: could not inspect {_apw.name}: {exc}", file=sys.stderr)
        continue

    if not raw_pkg_name:
        print(f"Warning: skipping {_apw.name}: could not detect package name", file=sys.stderr)
        continue

    # An apworld may ship its sources under a display-name folder that is not a valid Python
    # identifier ("Twilight Princess"). Sanitize and rename it below instead of skipping, so its
    # option types and location list are introspected like any other world (issue #278).
    pkg_name = raw_pkg_name if raw_pkg_name.isidentifier() else _sanitize_pkg_name(raw_pkg_name)
    if not pkg_name or not pkg_name.isidentifier():
        print(f"Warning: skipping {_apw.name}: invalid package name '{raw_pkg_name}'", file=sys.stderr)
        continue

    world_mod_name = f"worlds.{pkg_name}"
    if world_mod_name in sys.modules:
        continue

    _tmp_dir = tempfile.mkdtemp(prefix="apworld_")
    atexit.register(shutil.rmtree, _tmp_dir, True)
    with zipfile.ZipFile(str(_apw)) as _zf:
        _zf.extractall(_tmp_dir)
    if raw_pkg_name != pkg_name:
        _raw_dir = os.path.join(_tmp_dir, raw_pkg_name)
        if os.path.isdir(_raw_dir):
            os.rename(_raw_dir, os.path.join(_tmp_dir, pkg_name))

    _worlds_stub.__path__.insert(0, _tmp_dir)
    try:
        import_world(
            world_mod_name,
            on_stub=lambda name, apw=_apw.name: print(
                f"Note: stubbed missing module '{name}' for {apw}", file=sys.stderr),
        )
        _loaded_pkg_names.append(pkg_name)
    except Exception as exc:
        _worlds_stub.__path__.remove(_tmp_dir)
        print(f"Warning: failed to load {_apw.name} ({pkg_name}): {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)

# ── Find registered game ──────────────────────────────────────────────────────

_apworld_prefixes = tuple(f"worlds.{p}" for p in _loaded_pkg_names)
apworld_games = [
    g for g, cls in AutoWorldRegister.world_types.items()
    if getattr(cls, "__module__", "").startswith(_apworld_prefixes)
]
if not apworld_games:
    print("No game registered from the apworld(s) in --world_directory", file=sys.stderr)
    sys.exit(1)

game = apworld_games[0]
world_cls = AutoWorldRegister.world_types[game]

# ── Classify option types ─────────────────────────────────────────────────────

# Import base option classes; gracefully skip any that don't exist in this AP version.
def _try_import(name: str):
    try:
        mod = importlib.import_module("Options")
        return getattr(mod, name, None)
    except Exception:
        return None

_OptionDict = _try_import("OptionDict")
_Toggle     = _try_import("Toggle")
_Choice     = _try_import("Choice")
_Range      = _try_import("Range")
_OptionList = _try_import("OptionList")
_FreeText   = _try_import("FreeText")


def classify(field_type: type) -> str | None:
    """Map a Python option class to our TemplateOptionType string."""
    if not isinstance(field_type, type):
        return None
    try:
        # OptionDict before Choice - OptionDict may not inherit from Choice but
        # check order matters if a future version changes the hierarchy.
        if _OptionDict and issubclass(field_type, _OptionDict):
            return "weights"
        # Toggle before Choice - Toggle subclasses Choice in some AP versions.
        if _Toggle and issubclass(field_type, _Toggle):
            return "toggle"
        if _Choice and issubclass(field_type, _Choice):
            return "choice"
        if _Range and issubclass(field_type, _Range):
            return "range"
        if _OptionList and issubclass(field_type, _OptionList):
            return "text"
        if _FreeText and issubclass(field_type, _FreeText):
            return "text"
    except TypeError:
        pass
    return None


# ── Inspect options_dataclass ─────────────────────────────────────────────────

import dataclasses
import typing

result: dict[str, dict] = {}

if hasattr(world_cls, "options_dataclass") and world_cls.options_dataclass is not None:
    try:
        hints = typing.get_type_hints(world_cls.options_dataclass)
    except Exception:
        hints = {}

    for field in dataclasses.fields(world_cls.options_dataclass):
        # Prefer resolved hint; fall back to raw field.type if it's not a string annotation.
        field_type = hints.get(field.name)
        if field_type is None and not isinstance(field.type, str):
            field_type = field.type
        if field_type is None:
            continue

        typ = classify(field_type)
        if typ is None:
            continue

        entry: dict = {"type": typ}

        if typ == "weights":
            try:
                default = field_type.default
                if isinstance(default, dict):
                    entry["defaultWeights"] = {str(k): int(v) for k, v in default.items()}
            except Exception:
                pass

        if typ == "range":
            # Authoritative bounds + default from the Range option class
            # (range_start / range_end / default), so consumers don't have to
            # scrape template comments or guess.
            try:
                range_start = getattr(field_type, "range_start", None)
                range_end = getattr(field_type, "range_end", None)
                range_default = getattr(field_type, "default", None)
                # bool is an int subclass; ranges never use it - exclude defensively.
                if isinstance(range_start, int) and not isinstance(range_start, bool):
                    entry["min"] = range_start
                if isinstance(range_end, int) and not isinstance(range_end, bool):
                    entry["max"] = range_end
                if isinstance(range_default, int) and not isinstance(range_default, bool):
                    entry["default"] = range_default
            except Exception:
                pass

        result[field.name] = entry

# ── Extract location names (static list from the World class) ──────────────────
# location_name_to_id maps every network-addressable check to its id; its keys are
# the canonical location names used by priority_locations / exclude_locations /
# start_location_hints. This is the *static* list: options-dependent checks are not
# reflected here, so consumers must treat it as a suggestion hint, not a source of truth.
try:
    _loc_map = getattr(world_cls, "location_name_to_id", None) or {}
    locations = sorted(str(name) for name in _loc_map.keys())
except Exception:
    locations = []

print(json.dumps({"options": result, "locations": locations}))
