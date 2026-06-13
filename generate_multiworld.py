#!/usr/bin/env python3
"""
Server-side Archipelago multiworld generator.

Uses Python 3.13 + Archipelago source tree so custom .apworld files compiled
for Python 3.13 are loaded correctly (the ArchipelagoGenerate binary embeds
Python 3.12 and cannot load them).

Stubs out client-only third-party C extensions that apworlds import at module
level but are never needed server-side (dolphin_memory_engine, gclib, …).
"""
import atexit
import importlib.abc
import importlib.machinery
import json as _json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
import zipfile

# Kivy: suppress its argument parser and env-var hooks so it doesn't interfere
# with sys.argv (some worlds import kivy at module level for their client UI).
os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_NO_ENV_CONFIG", "1")

ARCH_SRC          = "/app/ArchipelagoSrc"
OFFICIAL_APWORLDS = pathlib.Path("/app/Archipelago/Archipelago/lib/worlds")
APWORLDS_IN       = pathlib.Path("/apworlds")

# ─── Specific pre-stubs ───────────────────────────────────────────────────────

_mu = types.ModuleType("ModuleUpdate")
_mu.update = lambda *a, **kw: None  # type: ignore[attr-defined]
sys.modules["ModuleUpdate"] = _mu

_winapi_stub = types.ModuleType("_winapi")
_winapi_stub.__getattr__ = lambda name: 0  # type: ignore[method-assign]
sys.modules["_winapi"] = _winapi_stub

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
_tk_stub = types.ModuleType("tkinter")
_tk_stub.__getattr__ = lambda _n: _tk_stub  # type: ignore[attr-defined]
for _tk_name in ("tkinter", "_tkinter", "tkinter.ttk", "tkinter.font",
                 "tkinter.messagebox", "tkinter.filedialog", "tkinter.colorchooser",
                 "tkinter.simpledialog", "tkinter.constants"):
    sys.modules.setdefault(_tk_name, _tk_stub)

# pkg_resources: setuptools 71+ no longer ships it as a standalone top-level
# package. Pre-populate sys.modules from pip's vendored copy so apworlds that
# call pkg_resources.resource_listdir() get the real implementation.
try:
    import pkg_resources  # noqa: F401
except ImportError:
    from pip._vendor import pkg_resources as _pr  # type: ignore[no-redef]
    sys.modules["pkg_resources"] = _pr

# ─── Source tree ──────────────────────────────────────────────────────────────
sys.path.insert(0, ARCH_SRC)

# ─── Pre-stub worlds package ──────────────────────────────────────────────────
# Inject a stub `worlds` package BEFORE anything can trigger worlds/__init__.py.
# worlds/__init__.py auto-loads ALL built-in worlds and calls get_data_package_data()
# on each. Worlds with stubbed C extensions register _Stub objects in their data,
# which are not JSON-serializable and crash data_package_checksum(). With this stub
# worlds/__init__.py never runs; worlds are loaded individually below.
_worlds_stub = types.ModuleType("worlds")
_worlds_stub.__path__ = [f"{ARCH_SRC}/worlds"]
_worlds_stub.__package__ = "worlds"
sys.modules["worlds"] = _worlds_stub

# ─── Auto-stub: silence client-only third-party imports ──────────────────────
_ARCHIP_ROOTS = frozenset({
    "BaseClasses",
    "entrance_rando",
    "Fill",
    "Generate",
    "Main",
    "MultiServer",
    "NetUtils",
    "Options",
    "Patch",
    "settings",
    "Utils",
    "WebHost",
    "worlds",
})


class _Stub:
    def __getattr__(self, _n): return _Stub()
    def __call__(self, *a, **kw): return _Stub()
    def __mro_entries__(self, bases): return (object,)
    def __getitem__(self, key): return _Stub()
    def __setitem__(self, key, value): pass
    def __delitem__(self, key): pass
    def __contains__(self, item): return False
    def __neg__(self): return _Stub()
    def __pos__(self): return _Stub()
    def __abs__(self): return _Stub()
    def __invert__(self): return _Stub()
    def __add__(self, o): return _Stub()
    def __radd__(self, o): return _Stub()
    def __sub__(self, o): return _Stub()
    def __rsub__(self, o): return _Stub()
    def __mul__(self, o): return _Stub()
    def __rmul__(self, o): return _Stub()
    def __truediv__(self, o): return _Stub()
    def __rtruediv__(self, o): return _Stub()
    def __floordiv__(self, o): return _Stub()
    def __rfloordiv__(self, o): return _Stub()
    def __mod__(self, o): return _Stub()
    def __rmod__(self, o): return _Stub()
    def __pow__(self, o, m=None): return _Stub()
    def __rpow__(self, o): return _Stub()
    def __matmul__(self, o): return _Stub()
    def __rmatmul__(self, o): return _Stub()
    def __and__(self, o): return _Stub()
    def __rand__(self, o): return _Stub()
    def __or__(self, o): return _Stub()
    def __ror__(self, o): return _Stub()
    def __xor__(self, o): return _Stub()
    def __rxor__(self, o): return _Stub()
    def __lshift__(self, o): return _Stub()
    def __rlshift__(self, o): return _Stub()
    def __rshift__(self, o): return _Stub()
    def __rrshift__(self, o): return _Stub()
    def __lt__(self, o): return False
    def __le__(self, o): return False
    def __gt__(self, o): return False
    def __ge__(self, o): return False
    def __eq__(self, o): return isinstance(o, _Stub)
    def __ne__(self, o): return not isinstance(o, _Stub)
    def __bool__(self): return False
    def __int__(self): return 0
    def __float__(self): return 0.0
    def __complex__(self): return 0j
    def __index__(self): return 0
    def __str__(self): return ""
    def __repr__(self): return "stub"
    def __bytes__(self): return b""
    def __hash__(self): return 0
    def __iter__(self): return iter([])
    def __len__(self): return 0
    def items(self): return {}.items()
    def values(self): return {}.values()
    def keys(self): return {}.keys()


class _AutoStubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname: str, path, target=None):
        if fullname.split(".")[0] in _ARCHIP_ROOTS:
            return None
        return importlib.machinery.ModuleSpec(fullname, self)

    def create_module(self, spec):
        return types.ModuleType(spec.name)

    def exec_module(self, module):
        module.__getattr__ = lambda _n: _Stub()


sys.meta_path.append(_AutoStubFinder())


def _sanitize_pkg_name(name: str) -> str:
    import re as _re
    sanitized = _re.sub(r"[^A-Za-z0-9_]", "_", name)
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    return sanitized


# Populate the worlds stub now that _AutoStubFinder is in place so transitive
# imports from AutoWorld.py are stubbed correctly.
from worlds.AutoWorld import AutoWorldRegister, World  # noqa: E402
_worlds_stub.AutoWorldRegister = AutoWorldRegister
_worlds_stub.World = World
_worlds_stub.local_folder = f"{ARCH_SRC}/worlds"
_worlds_stub.user_folder = None
_worlds_stub.failed_world_loads = []
_worlds_stub.network_data_package = {"games": {}}


# ─── Host-gated world settings (story 27.11) ──────────────────────────────────
# Some worlds gate a *player* option behind a *host.yaml* setting of the same
# concept (e.g. Vampire Survivors `allow_unfair_characters`): generate_early
# raises unless the host opted in. We generate without a host.yaml, so those
# gates default off and any seed enabling such an option fails hard. We derive a
# host.yaml that enables the host *permission* gates (Bool, default False, named
# allow_*/enable_*) of the loaded worlds. A host setting only *permits* - the
# world still requires the player's own option for any content to appear - so
# enabling gates for loaded worlds is safe and never changes a seed unless a
# player opted in. Non-bool settings (RomFile/Executable/paths) and non-permission
# toggles are deliberately left untouched.

_HOST_GATE_PREFIXES = ("allow_", "enable_")


def _find_settings_groups(world_cls):
    """Return the settings.Group subclasses declared in a world's own package."""
    try:
        import settings as _settings_mod
    except Exception:
        return []
    _group_base = getattr(_settings_mod, "Group", None)
    if _group_base is None:
        return []
    _pkg = getattr(world_cls, "__module__", "") or ""
    if not _pkg:
        return []
    _found = []
    for _mod_name, _mod in list(sys.modules.items()):
        if not (_mod_name == _pkg or _mod_name.startswith(_pkg + ".")):
            continue
        for _attr in dir(_mod):
            try:
                _obj = getattr(_mod, _attr)
            except Exception:
                continue
            if (isinstance(_obj, type) and issubclass(_obj, _group_base)
                    and _obj is not _group_base and _obj not in _found):
                _found.append(_obj)
    return _found


def _collect_host_gates(group_cls):
    """Bool members defaulting False, named allow_*/enable_* (the permission gates)."""
    _gates = {}
    for _klass in group_cls.__mro__:
        # Stop at Archipelago's own settings base classes (module == "settings").
        if getattr(_klass, "__module__", "") == "settings":
            break
        for _name, _val in vars(_klass).items():
            if _name.startswith("_") or _name in _gates:
                continue
            if isinstance(_val, bool) and _val is False and _name.startswith(_HOST_GATE_PREFIXES):
                _gates[_name] = True
    return _gates


def derive_host_gate_settings(world_types):
    """Build {settings_key: {gate: True}} enabling host permission gates for the worlds.

    Pure introspection - no per-game list, no player<->host name mapping.
    """
    _host = {}
    for _world_cls in world_types.values():
        try:
            _key = getattr(_world_cls, "settings_key", None)
            if not isinstance(_key, str) or not _key:
                continue
            _gates = {}
            for _group in _find_settings_groups(_world_cls):
                _gates.update(_collect_host_gates(_group))
            if _gates:
                _host.setdefault(_key, {}).update(_gates)
        except Exception as _e:
            print(f"Warning: host-gate introspection failed for "
                  f"{getattr(_world_cls, 'game', '?')}: {_e}", file=sys.stderr)
    return _host


def write_host_gate_yaml(host_settings, path):
    """Merge the derived gate sections into host.yaml at `path` (create if absent)."""
    if not host_settings:
        return
    import yaml as _yaml
    _existing = {}
    try:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as _f:
                _loaded = _yaml.safe_load(_f)
            if isinstance(_loaded, dict):
                _existing = _loaded
    except Exception as _e:
        print(f"Warning: could not read existing host.yaml at {path}: {_e}", file=sys.stderr)
        _existing = {}
    for _key, _gates in host_settings.items():
        _section = _existing.get(_key)
        if not isinstance(_section, dict):
            _section = {}
        _section.update(_gates)
        _existing[_key] = _section
    try:
        with open(path, "w", encoding="utf-8") as _f:
            _yaml.safe_dump(_existing, _f, default_flow_style=False, sort_keys=True)
        print(f"DEBUG host gates enabled: {host_settings}", file=sys.stderr)
    except Exception as _e:
        print(f"Warning: could not write host.yaml at {path}: {_e}", file=sys.stderr)


# ─── Run generation ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import Generate
    from Main import main as ERmain
    _worlds_pkg = sys.modules["worlds"]

    def _install_apworld_requirements(tmp_dir: str, pkg_name: str) -> None:
        for candidate in [
            os.path.join(tmp_dir, pkg_name, "requirements.txt"),
            os.path.join(tmp_dir, "requirements.txt"),
        ]:
            if not os.path.isfile(candidate):
                continue
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", candidate, "--quiet"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                print(
                    f"Warning: pip install failed for {pkg_name}: {result.stderr.strip()}",
                    file=sys.stderr,
                )
                return
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

    def _load_apworlds_from(apworld_dir: pathlib.Path) -> None:
        if not apworld_dir.is_dir():
            return
        for _apw in sorted(apworld_dir.glob("*.apworld")):
            try:
                with zipfile.ZipFile(str(_apw)) as _zf:
                    _entries = _zf.namelist()
                _raw_pkg = None
                for _e in _entries:
                    _parts = _e.replace("\\", "/").split("/")
                    if len(_parts) == 2 and _parts[1] == "__init__.py" and _parts[0]:
                        _raw_pkg = _parts[0]
                        break
                if _raw_pkg is None:
                    for _e in _entries:
                        _c = _e.replace("\\", "/").split("/")[0]
                        if _c:
                            _raw_pkg = _c
                            break
            except Exception as _e:
                print(f"Warning: could not inspect {_apw.name}: {_e}", file=sys.stderr)
                continue
            if not _raw_pkg:
                print(f"Warning: skipping {_apw.name}: could not detect package name", file=sys.stderr)
                continue
            _pkg = _raw_pkg if _raw_pkg.isidentifier() else _sanitize_pkg_name(_raw_pkg)
            if not _pkg or not _pkg.isidentifier():
                print(f"Warning: skipping {_apw.name}: invalid package name '{_raw_pkg}'", file=sys.stderr)
                continue
            _mod = f"worlds.{_pkg}"
            if _mod in sys.modules:
                continue
            _tmp_dir = tempfile.mkdtemp(prefix="apworld_")
            atexit.register(shutil.rmtree, _tmp_dir, True)
            with zipfile.ZipFile(str(_apw)) as _zf2:
                for _member in _zf2.infolist():
                    _member.filename = _member.filename.replace("\\", "/")
                    _zf2.extract(_member, _tmp_dir)
            if _raw_pkg != _pkg:
                _raw_dir = os.path.join(_tmp_dir, _raw_pkg)
                if os.path.isdir(_raw_dir):
                    os.rename(_raw_dir, os.path.join(_tmp_dir, _pkg))
            _install_apworld_requirements(_tmp_dir, _pkg)
            # Expose bundled top-level packages (deps at zip root) to the import system.
            sys.path.insert(0, _tmp_dir)
            _worlds_pkg.__path__.append(_tmp_dir)
            try:
                importlib.import_module(_mod)
            except Exception as _e:
                _worlds_pkg.__path__.remove(_tmp_dir)
                sys.path.remove(_tmp_dir)
                print(f"Warning: failed to load {_apw.name} ({_pkg}): {_e}", file=sys.stderr)

    # Consume --world_directory and strip it from sys.argv so Generate.main()
    # does not see it as an unrecognised argument.
    _custom_dir: pathlib.Path | None = None
    _i = 1
    while _i < len(sys.argv):
        if sys.argv[_i] == "--world_directory" and _i + 1 < len(sys.argv):
            _custom_dir = pathlib.Path(sys.argv[_i + 1])
            del sys.argv[_i:_i + 2]
        else:
            _i += 1

    # Build a combined apworlds directory:
    #   1. Official apworlds from the binary release (always present, even if unused)
    #   2. Custom apworlds from /apworlds volume (if mounted)
    #   3. Custom apworlds from --world_directory (session-specific)
    # Custom files with the same name as an official one override it.
    _combined = pathlib.Path(tempfile.mkdtemp(prefix="apworlds_"))

    for _apw in sorted(OFFICIAL_APWORLDS.glob("*.apworld")):
        shutil.copy2(_apw, _combined / _apw.name)

    for _src in [APWORLDS_IN, _custom_dir]:
        if _src and _src.is_dir():
            for _apw in sorted(_src.glob("*.apworld")):
                shutil.copy2(_apw, _combined / _apw.name)

    print(f"DEBUG combined apworlds: {sorted(p.name for p in _combined.glob('*.apworld'))}",
          file=sys.stderr)

    _load_apworlds_from(_combined)

    # Rebuild network_data_package with only successfully loaded worlds.
    # Per-world try-except guards against worlds that loaded with C-extension stubs
    # producing non-JSON-serializable values in get_data_package_data().
    for _cls in _worlds_pkg.AutoWorldRegister.world_types.values():
        try:
            _worlds_pkg.network_data_package["games"][_cls.game] = _cls.get_data_package_data()
        except Exception as _e:
            print(f"Warning: data package for {_cls.game} skipped: {_e}", file=sys.stderr)

    print(f"DEBUG worlds loaded: {sorted(__import__('worlds').AutoWorldRegister.world_types)}",
          file=sys.stderr)

    # Story 27.11: enable host-gated permission settings (e.g. VS
    # allow_unfair_characters) so player options requiring a host.yaml opt-in
    # don't fail generation. Must run BEFORE Generate.main() loads settings.
    try:
        from Utils import user_path as _user_path
        _host_yaml_path = _user_path("host.yaml")
        _gate_settings = derive_host_gate_settings(_worlds_pkg.AutoWorldRegister.world_types)
        write_host_gate_yaml(_gate_settings, _host_yaml_path)
    except Exception as _e:
        print(f"Warning: host-gate derivation skipped: {_e}", file=sys.stderr)

    erargs, seed = Generate.main()
    ERmain(erargs, seed)

    # Print the generated output filename to stdout for the orchestrator to capture.
    _out_dir = pathlib.Path(getattr(erargs, "outputpath", "/data/output"))
    _out_files = sorted(_out_dir.glob("*.zip")) or sorted(_out_dir.glob("*.archipelago"))
    if _out_files:
        print(_out_files[0].name, flush=True)
    else:
        print(f"ERROR: no output file found in {_out_dir}", file=sys.stderr)
        sys.exit(1)
