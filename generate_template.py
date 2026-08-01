#!/usr/bin/env python3
"""Generate Archipelago YAML template for a given game."""
import argparse
import atexit
import importlib
import json as _json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import traceback
import types
import zipfile

# Kivy: suppress its argument parser and env-var hooks so it doesn't interfere
# with sys.argv (some worlds import kivy at module level for their client UI).
os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_NO_ENV_CONFIG", "1")

ARCH_SRC = "/app/ArchipelagoSrc"

# ─── Specific pre-stubs ───────────────────────────────────────────────────────
# Placed into sys.modules BEFORE sys.path is modified so these stubs always win.

# ModuleUpdate: Archipelago's own auto-updater - no-op in ephemeral containers.
_mu = types.ModuleType("ModuleUpdate")
_mu.update = lambda *a, **kw: None  # type: ignore[attr-defined]
sys.modules["ModuleUpdate"] = _mu

# _winapi: Windows-only C extension imported unconditionally by Python 3.13's
# subprocess.py on some code paths. Returns 0 for any attribute lookup.
_winapi_stub = types.ModuleType("_winapi")
_winapi_stub.__getattr__ = lambda name: 0  # type: ignore[method-assign]
sys.modules["_winapi"] = _winapi_stub

# orjson: use real package if installed; fall back to stdlib shim if not.
# Patch orjson.orjson = orjson so `from orjson import orjson` works regardless
# (the Rust extension doesn't expose itself as an attribute).
try:
    import orjson as _orjson  # noqa: F401
except ImportError:
    _orjson = types.ModuleType("orjson")
    _orjson.loads = _json.loads  # type: ignore[attr-defined]
    _orjson.dumps = lambda obj, **kw: _json.dumps(obj, default=str).encode()  # type: ignore[attr-defined]
    sys.modules["orjson"] = _orjson
if not hasattr(_orjson, "orjson"):
    _orjson.orjson = _orjson  # type: ignore[attr-defined]

# tkinter / _tkinter: GUI toolkit not available in headless containers.
# The .so fails to load (missing libtk), so we must stub before the stdlib
# finder tries to import it.
_tk_stub = types.ModuleType("tkinter")
_tk_stub.__getattr__ = lambda _n: _tk_stub  # type: ignore[attr-defined]
_tk_stub.__call__ = lambda *a, **kw: _tk_stub  # type: ignore[attr-defined]
for _tk_name in ("tkinter", "_tkinter", "tkinter.ttk", "tkinter.font",
                 "tkinter.messagebox", "tkinter.filedialog", "tkinter.colorchooser",
                 "tkinter.simpledialog", "tkinter.constants"):
    sys.modules.setdefault(_tk_name, _tk_stub)

# pkg_resources: setuptools 71+ no longer ships it as a standalone top-level
# package (removed from site-packages). Pre-populate sys.modules from pip's
# vendored copy so apworlds that call pkg_resources.resource_listdir() (e.g.
# pokemon_emerald) get the real implementation instead of an auto-stub.
try:
    import pkg_resources  # noqa: F401 - already installed (older setuptools)
except ImportError:
    from pip._vendor import pkg_resources as _pr  # type: ignore[no-redef]
    sys.modules["pkg_resources"] = _pr

# ─── Worlds stub + source tree ────────────────────────────────────────────────
# Inject a stub `worlds` package so importing worlds.AutoWorld does NOT trigger
# worlds/__init__.py's auto-loading of all games (which causes Logic Mixin
# conflicts when a custom apworld redefines the same game as a built-in world).
_worlds_stub = types.ModuleType("worlds")
_worlds_stub.__path__ = [f"{ARCH_SRC}/worlds"]
_worlds_stub.__package__ = "worlds"
# Point at the real worlds/__init__.py so apworlds that resolve files relative to
# `worlds.__file__` work (e.g. yugiohgx: os.path.dirname(worlds.__file__)/_bizhawk.apworld).
# Without it, the __getattr__ below would try to import `worlds.__file__` and raise.
_worlds_stub.__file__ = f"{ARCH_SRC}/worlds/__init__.py"


def _worlds_getattr(name: str) -> object:
    """Lazily import worlds.<name> when accessed as an attribute.

    Apworlds that write ``worlds.Files.APDeltaPatch`` (attribute access instead
    of an explicit ``import worlds.Files``) hit this path.  We try to import the
    sub-module from ARCH_SRC/worlds/ and cache it on the stub so the next access
    is free.  If the sub-module doesn't exist we raise AttributeError normally.
    """
    # Dunder attributes are never sub-modules; behave like a normal module and let
    # the standard machinery handle them (those set on the stub resolve via __dict__
    # without reaching here) instead of attempting a bogus `worlds.__x__` import.
    if name.startswith("__") and name.endswith("__"):
        raise AttributeError(f"module 'worlds' has no attribute {name!r}")
    full = f"worlds.{name}"
    if full in sys.modules:
        mod = sys.modules[full]
    else:
        try:
            mod = importlib.import_module(full)
        except ImportError:
            raise AttributeError(f"module 'worlds' has no attribute {name!r}")
    setattr(_worlds_stub, name, mod)
    return mod


_worlds_stub.__getattr__ = _worlds_getattr  # type: ignore[attr-defined]
sys.modules["worlds"] = _worlds_stub
sys.path.insert(0, ARCH_SRC)

parser = argparse.ArgumentParser()
parser.add_argument("--outputpath", required=True)
parser.add_argument("--world_directory", default=None)
args = parser.parse_args()

# Load framework without triggering worlds/__init__.py
from worlds.AutoWorld import AutoWorldRegister, World  # noqa: E402
from Utils import local_path, user_path  # noqa: E402

# Expose on the stub so other modules can do `from worlds import AutoWorldRegister`
_worlds_stub.AutoWorldRegister = AutoWorldRegister
_worlds_stub.World = World

# Patch Options.Choice metaclass to allow apworlds that define `option_random`.
# Archipelago reserves "random" as a keyword; some apworlds use it anyway.
# On AssertionError we strip option_random* and retry so the world still loads.
import Options as _Options_mod  # noqa: E402
_ChoiceMeta = type(_Options_mod.Choice)
_orig_choice_meta_new = _ChoiceMeta.__new__

def _permissive_choice_meta_new(mcs, name, bases, namespace, **kwargs):
    try:
        return _orig_choice_meta_new(mcs, name, bases, namespace, **kwargs)
    except AssertionError as _exc:
        if "random" in str(_exc).lower():
            _filtered = {k: v for k, v in namespace.items() if not k.startswith("option_random")}
            return _orig_choice_meta_new(mcs, name, bases, _filtered, **kwargs)
        raise

_ChoiceMeta.__new__ = _permissive_choice_meta_new

# Expose worlds/__init__.py public symbols that apworlds import directly.
# Without these, apworlds doing `from worlds import user_folder` crash with
# ImportError even though their logic doesn't actually use the value.
_worlds_stub.local_folder = f"{ARCH_SRC}/worlds"
_user_folder = user_path("worlds") if user_path() != local_path() else user_path("custom_worlds")
try:
    os.makedirs(_user_folder, exist_ok=True)
except OSError:
    _user_folder = None
_worlds_stub.user_folder = _user_folder
_worlds_stub.failed_world_loads = []

# ─── World imports: honest first, stub only what is truly missing ────────────
# See apworld_import.py: nothing is stubbed up front, so a world that ships its own
# fallback for a missing dependency (a vendored copy, a try/except ImportError) takes it
# exactly as it would on a desktop Archipelago. Only a module proven missing by a failed
# import gets stubbed, and only for the retry.
from apworld_import import import_world  # noqa: E402


def _sanitize_pkg_name(name: str) -> str:
    """Convert a directory name to a valid Python identifier (spaces → underscores)."""
    import re as _re
    sanitized = _re.sub(r"[^A-Za-z0-9_]", "_", name)
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    return sanitized


def _install_apworld_requirements(tmp_dir: str, pkg_name: str) -> None:
    """Install requirements.txt bundled inside an apworld and evict pre-stubs.

    Packages are installed into the package directory (--target pkg_dir) so that
    relative imports like `from . import map_rando_app_data` resolve correctly:
    the installed module ends up inside pkg_dir and therefore inside the package's
    __path__, which is what Python searches for relative imports.
    """
    for candidate in [
        os.path.join(tmp_dir, pkg_name, "requirements.txt"),
        os.path.join(tmp_dir, "requirements.txt"),
    ]:
        if not os.path.isfile(candidate):
            continue
        pkg_dir = os.path.join(tmp_dir, pkg_name)
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", candidate,
             "--target", pkg_dir, "--quiet"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            # The container is sealed (PIP_NO_INDEX=1 in the image): optional
            # play-time deps can never install there, so failure is the norm and
            # not worth logging. A world whose gen-time deps are truly missing
            # still surfaces through the "failed to load" warning at import.
            if not os.environ.get("PIP_NO_INDEX"):
                print(
                    f"Warning: pip install failed for {pkg_name}: {result.stderr.strip()}",
                    file=sys.stderr,
                )
            return
        # Make transitive deps of the installed packages importable as top-level.
        if pkg_dir not in sys.path:
            sys.path.insert(0, pkg_dir)
        # Evict installed packages from sys.modules so fresh imports override pre-stubs.
        with open(candidate, encoding="utf-8") as f:
            installed = {
                line.strip().split("==")[0].split(">=")[0].split("<=")[0]
                           .split("!=")[0].split("[")[0].strip().lower().replace("-", "_")
                for line in f
                if line.strip() and not line.startswith("#") and not line.startswith("-")
            }
        for key in [k for k in sys.modules if k.split(".")[0].lower().replace("-", "_") in installed]:
            del sys.modules[key]
        return


# ── Phase 1: load custom apworlds ────────────────────────────────────────────
# Track pkg_names of apworlds we successfully load so we can filter by __module__.
_loaded_pkg_names: list[str] = []

# Strategy: add the apworld ZIP to _worlds_stub.__path__ then import as
# worlds.<pkg_name>. This makes importlib.resources.files("worlds.<pkg_name>")
# work correctly for apworlds that call it at module level.
if args.world_directory:
    for _apw in sorted(pathlib.Path(args.world_directory).glob("*.apworld")):
        try:
            with zipfile.ZipFile(str(_apw)) as zf:
                entries = zf.namelist()
                if not entries:
                    continue
                # Find the Python package directory: the first top-level folder
                # that directly contains __init__.py. This is more robust than
                # taking entries[0], which may be a non-package file (e.g.
                # archipelago.json, .gitattributes) that sorts before the package.
                # Find the raw directory name (may contain spaces or other
                # non-identifier chars - we will sanitize below).
                raw_pkg = None
                for _e in entries:
                    _norm = _e.replace("\\", "/")
                    _parts = _norm.split("/")
                    if len(_parts) == 2 and _parts[1] == "__init__.py" and _parts[0]:
                        raw_pkg = _parts[0]
                        break
                if raw_pkg is None:
                    for _e in entries:
                        _candidate = _e.replace("\\", "/").split("/")[0]
                        if _candidate:
                            raw_pkg = _candidate
                            break
        except Exception as exc:
            print(f"Warning: could not inspect {_apw.name}: {exc}", file=sys.stderr)
            continue

        if not raw_pkg:
            print(f"Warning: skipping {_apw.name}: could not detect package name", file=sys.stderr)
            continue

        # Sanitize: replace non-identifier chars with underscores.
        pkg_name = raw_pkg if raw_pkg.isidentifier() else _sanitize_pkg_name(raw_pkg)
        if not pkg_name or not pkg_name.isidentifier():
            print(f"Warning: skipping {_apw.name}: invalid package name '{raw_pkg}'", file=sys.stderr)
            continue

        world_mod_name = f"worlds.{pkg_name}"
        if world_mod_name in sys.modules:
            continue

        # Extract to a temp directory so __file__-based open() calls work.
        # Loading the ZIP directly via zipimport sets __file__ to a path inside
        # the archive (e.g. /tmp/xxx.apworld/raft/module.pyc), which is not a
        # real filesystem path - open() on it fails with NotADirectoryError.
        _tmp_dir = tempfile.mkdtemp(prefix="apworld_")
        atexit.register(shutil.rmtree, _tmp_dir, True)
        with zipfile.ZipFile(str(_apw)) as _zf:
            for _member in _zf.infolist():
                _member.filename = _member.filename.replace("\\", "/")
                _zf.extract(_member, _tmp_dir)

        # Rename the package directory to its sanitized name if needed
        # (e.g. "Twilight Princess" → "Twilight_Princess").
        if raw_pkg != pkg_name:
            _raw_dir = os.path.join(_tmp_dir, raw_pkg)
            _safe_dir = os.path.join(_tmp_dir, pkg_name)
            if os.path.isdir(_raw_dir):
                os.rename(_raw_dir, _safe_dir)

        # Install apworld-specific dependencies before importing.
        _install_apworld_requirements(_tmp_dir, pkg_name)

        # Expose bundled top-level packages (deps at zip root) to the import system.
        sys.path.insert(0, _tmp_dir)

        # Insert at position 0 so the custom apworld takes priority over any
        # built-in world with the same name in ARCH_SRC/worlds.
        _worlds_stub.__path__.insert(0, _tmp_dir)

        # Redirect stdout → stderr during import so that apworlds that call
        # print() at module level (e.g. during config processing) don't pollute
        # the YAML output we capture from stdout.
        _real_stdout = sys.stdout
        sys.stdout = sys.stderr
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
        finally:
            sys.stdout = _real_stdout

# ── Pick the game registered by the apworld ──────────────────────────────────
# Filter by __module__ to exclude built-in worlds pulled in as transitive imports
# (e.g. an apworld that imports worlds.archipelago would also register "Archipelago").
_apworld_prefixes = tuple(f"worlds.{p}" for p in _loaded_pkg_names)
apworld_games = [
    g for g, cls in AutoWorldRegister.world_types.items()
    if getattr(cls, "__module__", "").startswith(_apworld_prefixes)
]
if not apworld_games:
    print("No game registered from the apworld(s) in --world_directory", file=sys.stderr)
    sys.exit(1)
game = apworld_games[0]

# ── Generate template via Archipelago's official template engine ─────────────
# Filter world_types to only the target game so the function outputs one file.
_all_world_types = dict(AutoWorldRegister.world_types)
AutoWorldRegister.world_types.clear()
AutoWorldRegister.world_types[game] = _all_world_types[game]

output = pathlib.Path(args.outputpath)
output.mkdir(parents=True, exist_ok=True)

from Options import generate_yaml_templates  # noqa: E402
try:
    generate_yaml_templates(str(output))
except Exception as _tpl_exc:
    print(f"generate_yaml_templates raised: {type(_tpl_exc).__name__}: {_tpl_exc}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)

# Restore world_types in case something downstream still needs them.
AutoWorldRegister.world_types.update(_all_world_types)

yaml_files = sorted(output.glob("*.yaml"))
if yaml_files:
    print(yaml_files[0].read_text(encoding="utf-8"))
else:
    print(f"No template generated for '{game}'", file=sys.stderr)
    sys.exit(1)
