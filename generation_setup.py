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
