"""Preparation shared by every script that loads apworlds or generates (story 9.55).

Four scripts load apworlds - generate_template.py, introspect_options.py, generate_multiworld.py and
reachable.py - and two of them run Generate. Each carried its own copy of this preparation, and the
copies drifted: reachable.py never had the permissive Choice metaclass nor the host permission gates,
so a run holding rune4, smash64 or untitled_goose_game, or a player option behind a host gate,
generated in production and then had no reachability at all. One module, called by all of them.

No import-time side effects: importing this module changes nothing until a function is called (the
Dockerfile imports it at build time to fail a missing COPY there rather than in production).
"""
import os
import re
import sys


# ─── Permissive Choice metaclass ──────────────────────────────────────────────
# Archipelago reserves "random" as a Choice keyword; some apworlds define `option_random` anyway and
# the metaclass asserts. On that assertion we strip the offending members and retry, so the world
# still loads - rune4, smash64 and untitled_goose_game all need this. Strictly failure-reducing: it
# only runs where the original raised.

def install_permissive_choice_meta(options_module=None):
    """Arm the permissive metaclass on `Options.Choice`. Idempotent."""
    if options_module is None:
        import Options as options_module
    choice_meta = type(options_module.Choice)
    original = choice_meta.__new__
    if getattr(original, "_archilan_permissive", False):
        return

    def _permissive_new(mcs, name, bases, namespace, **kwargs):
        try:
            return original(mcs, name, bases, namespace, **kwargs)
        except AssertionError as exc:
            if "random" in str(exc).lower():
                filtered = {k: v for k, v in namespace.items() if not k.startswith("option_random")}
                return original(mcs, name, bases, filtered, **kwargs)
            raise

    _permissive_new._archilan_permissive = True
    choice_meta.__new__ = _permissive_new


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


def apply_host_gates(world_types):
    """Open the host permission gates of the loaded worlds in host.yaml, before Generate reads it.

    Settings are read once and cached (settings.get_settings). A world that read them before the
    gates were written would keep the cached copy, gates off, and Generate would raise as if nothing
    had been done: the cache is dropped so the next read picks the gates up."""
    gates = derive_host_gate_settings(world_types)
    if not gates:
        return
    from Utils import user_path
    import settings

    write_host_gate_yaml(gates, user_path("host.yaml"))
    if hasattr(settings.get_settings, "_cache"):
        delattr(settings.get_settings, "_cache")


# ─── Accessibility not met: a warning, as the official Launcher does (story 38.12) ─────────────────────
# `MultiWorld.fulfills_accessibility` raises FillError under `if __debug__:`, otherwise logs a warning and
# returns False; `Main.main` then fails only if the game is unbeatable. The Launcher is frozen optimized
# (`__debug__` false) and generates such a game; this image runs plain Python and failed on it - a world that
# "works locally" was refused here. Only this check is softened: `python -O` would also drop every `assert`
# of Archipelago, "Duplicate item reference" among them, which guards against a truly broken game.

ACCESSIBILITY_MESSAGE = "Could not access required locations for accessibility check."
WARNING_SENTINEL = "###ARCHILAN-WARNING###"
WARNING_MESSAGE_MAX = 1200


def soften_accessibility_check(multiworld_cls=None, fill_error_cls=None):
    """Make an unmet accessibility check return False, as in an optimized build. Idempotent.

    Returns the list the warnings are recorded in (one record per unmet check):
    `{"type": "accessibility", "message": ..., "missing": [location names]}`.
    """
    if multiworld_cls is None:
        from BaseClasses import MultiWorld as multiworld_cls
    if fill_error_cls is None:
        from Fill import FillError as fill_error_cls

    original = multiworld_cls.fulfills_accessibility
    if getattr(original, "_archilan_softened", None) is not None:
        return original._archilan_softened

    warnings = []

    def _softened(self, *args, **kwargs):
        try:
            return original(self, *args, **kwargs)
        except fill_error_cls as exc:
            # The placements of the whole game follow the missing locations: left out, and the rest bounded.
            message = " ".join(str(exc).split("All Placements:")[0].split())[:WARNING_MESSAGE_MAX]
            if not message.startswith(ACCESSIBILITY_MESSAGE):
                raise
            warnings.append({"type": "accessibility", "message": message, "missing": _missing_locations(message)})
            print(f"Warning: {message}", file=sys.stderr, flush=True)
            return False

    _softened._archilan_softened = warnings
    multiworld_cls.fulfills_accessibility = _softened
    return warnings


def _missing_locations(message):
    start = message.find("Missing: [")
    end = message.find("]", start)
    if start < 0 or end < 0:
        return []
    listed = message[start + len("Missing: ["):end]
    return [name.strip() for name in listed.split(",") if name.strip()]


def warning_line(record):
    """The machine-readable line the orchestrator reads on stderr, next to a successful generation."""
    import json

    return f"{WARNING_SENTINEL} {json.dumps(record, ensure_ascii=False)}"


def allow_as_dict_of_every_option(common_options_cls=None):
    """`options.as_dict()` of every option works, as in an optimized build (story 38.25). Idempotent.

    `CommonOptions.as_dict` asserts `len(option_names) < len(type_hints)`, "Specify only options you need": advice
    to authors, gone in the Windows Launcher (`__debug__` false), where Caves of Qud generates with all its options
    in slot data. Such a call is split into smaller ones, merged in the order asked; any other call is untouched.
    """
    if common_options_cls is None:
        from Options import CommonOptions as common_options_cls

    original = common_options_cls.as_dict
    if getattr(original, "_archilan_every_option", False):
        return

    def _as_dict(self, *option_names, **kwargs):
        size = len(type(self).type_hints) - 1
        if size < 1 or len(option_names) <= size:
            return original(self, *option_names, **kwargs)
        result = {}
        for start in range(0, len(option_names), size):
            result.update(original(self, *option_names[start:start + size], **kwargs))
        return result

    _as_dict._archilan_every_option = True
    common_options_cls.as_dict = _as_dict


def release_events_on_output_failure(autoworld_module=None):
    """A world whose output failed releases its `threading.Event`s, then the failure goes on (story 38.24). Idempotent.

    Ocarina of Time opens its ROM in `generate_output` before the `try/finally` that sets
    `collectible_flags_available`, and `fill_slot_data` waits on it with no timeout. A world built on it that skips
    the upfront ROM check (Master Quest Water Temple) then hung until the orchestrator's 5 minute deadline instead
    of failing on the missing file. Released, the waiters move on and `Main.main` surfaces the original error.
    """
    if autoworld_module is None:
        from worlds import AutoWorld as autoworld_module

    original = autoworld_module.call_single
    if getattr(original, "_archilan_releases_events", False):
        return

    import threading

    def _call_single(multiworld, method_name, player, *args):
        try:
            return original(multiworld, method_name, player, *args)
        except Exception:
            if method_name == "generate_output":
                for value in list(vars(multiworld.worlds[player]).values()):
                    if isinstance(value, threading.Event):
                        value.set()
            raise

    _call_single._archilan_releases_events = True
    autoworld_module.call_single = _call_single


def disable_file_dialogs(settings_module=None):
    """No file dialog during a generation (story 44.1).

    A host.yaml `Path` marked required and missing makes Archipelago open a file browser - kdialog, zenity,
    then tkinter, which is a stub here: the generation then broke further on, for an unrelated reason. With
    `no_gui`, Archipelago raises `FileNotFoundError: <file> does not exist, but <Group>.<key> is required`.
    """
    if settings_module is None:
        import settings as settings_module
    settings_module.no_gui = True


_REQUIRED_FILE_PATTERN = re.compile(r"^(?P<file>.*) does not exist, but (?P<group>\w+)\.(?P<key>\w+) is required$")


def required_file_record(exc, world_types):
    """What the failed generation asked for, when it failed on a required file (story 44.1); else None.

    `{"setting": "DK3Settings.rom_file", "file": "<default name>", "description", "extension", "md5s"}` -
    the description and MD5s are read on the world's own setting, and left out when it cannot be found.
    """
    if not isinstance(exc, FileNotFoundError):
        return None
    match = _REQUIRED_FILE_PATTERN.match(" ".join(str(exc).split()))
    if match is None:
        return None

    record = {
        "setting": f"{match['group']}.{match['key']}",
        "file": match["file"],
        "extension": os.path.splitext(match["file"])[1].lower(),
    }
    try:
        declared = _declared_path(match["group"], match["key"], world_types)
        if declared is not None:
            if getattr(declared, "description", None):
                record["description"] = str(declared.description)
            md5s = [m if isinstance(m, str) else m.hex() for m in getattr(declared, "md5s", None) or []]
            if md5s:
                record["md5s"] = md5s
    except Exception as _e:
        print(f"Warning: required file details skipped: {_e}", file=sys.stderr)

    return record


def declared_required_files(world_cls, settings_module=None):
    """The files a world declares it needs, read statically from its settings groups (story 44.3).

    A host.yaml `FilePath` is `required` unless the world says otherwise; executables are left out (a
    client concern). This is a superset: most of these files only serve the client, and only a failed
    generation proves one is needed to generate. Same record shape as `required_file_record`.
    Never raises: a world that cannot be read declares nothing.
    """
    try:
        if settings_module is None:
            import settings as settings_module
        file_path_cls = getattr(settings_module, "FilePath", None)
        if file_path_cls is None:
            return []
        records = []
        for group in _find_settings_groups(world_cls):
            seen = set()
            for klass in group.__mro__:
                for key, value in vars(klass).items():
                    if key in seen or key.startswith("_"):
                        continue
                    seen.add(key)
                    if not isinstance(value, file_path_cls):
                        continue
                    if not getattr(value, "required", False) or getattr(value, "is_exe", False):
                        continue
                    record = {
                        "setting": f"{group.__name__}.{key}",
                        "file": str(value),
                        "extension": os.path.splitext(str(value))[1].lower(),
                    }
                    if getattr(value, "description", None):
                        record["description"] = str(value.description)
                    md5s = [m if isinstance(m, str) else m.hex() for m in getattr(value, "md5s", None) or []]
                    if md5s:
                        record["md5s"] = md5s
                    records.append(record)
        return sorted(records, key=lambda r: r["setting"])
    except Exception as _e:
        print(f"Warning: required files of {getattr(world_cls, 'game', '?')} skipped: {_e}", file=sys.stderr)
        return []


REQUIRED_FILE_ARG = "--required_file"


def take_required_file_args(argv):
    """Consume every `--required_file <Group>.<key>=<path>` from `argv` (story 44.2); `{"Group.key": path}`.

    Removed from the list so Generate's own argument parser never sees them. A malformed value is dropped
    with a warning: the generation then fails on the missing file, with the verdict of story 44.1.
    """
    files = {}
    i = 1
    while i < len(argv):
        if argv[i] != REQUIRED_FILE_ARG or i + 1 >= len(argv):
            i += 1
            continue
        setting, sep, path = argv[i + 1].partition("=")
        del argv[i:i + 2]
        if not sep or not path or _SETTING_PATTERN.match(setting) is None:
            print(f"Warning: ignored malformed {REQUIRED_FILE_ARG} value", file=sys.stderr)
            continue
        files[setting] = path
    return files


_SETTING_PATTERN = re.compile(r"^\w+\.\w+$")


def required_file_host_settings(files, world_types):
    """host.yaml sections pointing each required setting at its provided file; and the settings no world declares.

    A failure names the settings group by its class (`DK3Settings`), host.yaml by the world's `settings_key`
    (`dkc3_options`): the world whose package declares that class gives the section.
    """
    host = {}
    unknown = []
    for setting, path in files.items():
        group_name, _, key = setting.partition(".")
        section = _settings_key_of(group_name, world_types)
        if section is None:
            unknown.append(setting)
            continue
        host.setdefault(section, {})[key] = path
    return host, unknown


def apply_required_files(files, world_types):
    """Point the required host.yaml settings at the files provided for this generation (story 44.2).

    Same path as the host gates: merged into host.yaml, then the settings cache dropped. A setting no loaded
    world declares is only a warning - the generation then fails on its own, with a readable verdict.
    """
    if not files:
        return
    host, unknown = required_file_host_settings(files, world_types)
    for setting in unknown:
        print(f"Warning: no loaded world declares the required file setting {setting}", file=sys.stderr)
    if not host:
        return
    from Utils import user_path
    import settings

    write_host_gate_yaml(host, user_path("host.yaml"))
    if hasattr(settings.get_settings, "_cache"):
        delattr(settings.get_settings, "_cache")


def _settings_key_of(group_name, world_types):
    for world_cls in (world_types or {}).values():
        key = getattr(world_cls, "settings_key", None)
        if not isinstance(key, str) or not key:
            continue
        if any(group.__name__ == group_name for group in _find_settings_groups(world_cls)):
            return key
    return None


def _declared_path(group_name, key, world_types):
    """The `Path` default a world's settings group declares under `key`, found by the group's class name."""
    for world_cls in (world_types or {}).values():
        for group in _find_settings_groups(world_cls):
            if group.__name__ != group_name:
                continue
            for klass in group.__mro__:
                if key in vars(klass):
                    return vars(klass)[key]
    return None


# ─── worlds.world_sources ─────────────────────────────────────────────────────
# Archipelago's worlds/__init__.py lists every world it found in `world_sources`, and some apworlds
# read it to detect an optional companion: pokemon_bw looks for pokemon_bw_dexsanitysanity with
# `from worlds import world_sources` at import time. Our scripts replace `worlds` with a stub that
# never runs that file, so the attribute must be furnished here or the import fails and the world
# never registers (story 38.21).
class WorldSource:
    """The fields of Archipelago's `worlds.WorldSource` (0.6.8) that a world can read."""

    def __init__(self, path, is_zip=False, relative=False):
        self.path = path
        self.is_zip = is_zip
        self.relative = relative

    @property
    def name(self):
        return os.path.splitext(os.path.basename(self.path))[0]

    @property
    def resolved_path(self):
        return self.path

    def __repr__(self):
        return f"WorldSource({self.path}, is_zip={self.is_zip}, relative={self.relative})"


def apworld_sources(directory):
    """One source per .apworld of `directory`, as Archipelago indexes them before loading any.

    Listed up front, not as each one loads: pokemon_bw sorts before pokemon_bw_dexsanitysanity, so a
    list grown during the loop would not yet hold the companion when pokemon_bw looks for it.
    """
    if not directory or not os.path.isdir(directory):
        return []
    return [
        WorldSource(os.path.join(directory, name), is_zip=True)
        for name in sorted(os.listdir(directory))
        if name.endswith(".apworld")
    ]
