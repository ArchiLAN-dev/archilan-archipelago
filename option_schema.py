"""Allowed sub-option values of an Archipelago `OptionDict`, from its declared `Schema`.

An OptionDict says nothing about what its sub-settings accept: Archipelago's base class carries
`valid_keys` (the sub-setting NAMES) and no value vocabulary at all. The single machine-readable
declaration the options API offers is `schema = Schema({...})` (the `schema` library), which the
options system already validates player input against.

Only *literal* validators are read here. A lambda, a `Use`, a regex or any other callable is a
program, not a vocabulary; deriving a list from one would be a guess, and a guessed list ends up
closing a dropdown in the player's face - worse than leaving the free text field alone. Docstrings
are deliberately not read for the same reason: they are prose, written by dozens of independent
world authors with no shared convention.

Kept out of `introspect_options.py` because that module runs its work at import time (it parses
argv and loads the Archipelago source); these functions are pure and unit-testable on their own.
"""
from __future__ import annotations

try:
    import schema as _schema_lib
except Exception:  # ships with Archipelago, but never fail introspection over its absence
    _schema_lib = None

# How deep a validator may nest before we stop unwrapping it.
_MAX_DEPTH = 3


def literal_value(value):
    """The wire form of a scalar a player could type, or None if it is not one."""
    # bool first - it is an int subclass, and a dict value round-trips through a YAML 1.1
    # reader where `true`/`on` are booleans. Offering them as strings is how a bool silently
    # becomes a string at generation, so a boolean vocabulary is left to the free text field.
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        return value or None
    if isinstance(value, int):
        return str(value)
    return None


def values_from_validator(validator, _depth=0):
    """The values a Schema validator accepts, or None when it declares no literal vocabulary."""
    if _schema_lib is None or _depth > _MAX_DEPTH:
        return None

    # Or(...) is a union, so *every* branch must be literal for the list to be complete. A
    # partial list is the dangerous case: it reads as authoritative while missing entries the
    # world actually accepts. Checked before And - Or subclasses it.
    if isinstance(validator, _schema_lib.Or):
        collected = []
        for arg in getattr(validator, "_args", ()):
            branch = values_from_validator(arg, _depth + 1)
            if not branch:
                return None
            collected.extend(branch)
        return collected or None

    # And(...) narrows (`And(str, lambda s: len(s) <= 7)`), it does not enumerate.
    if isinstance(validator, _schema_lib.And):
        return None

    # Schema(x): unwrap one level, unless x is a container - a nested schema describes a shape,
    # not a vocabulary.
    if isinstance(validator, _schema_lib.Schema):
        inner = getattr(validator, "_schema", None)
        if inner is None or isinstance(inner, (dict, list, tuple, set)):
            return None
        return values_from_validator(inner, _depth + 1)

    # A bare type (str, int) is a category, not a list of values.
    if isinstance(validator, type):
        return None

    single = literal_value(validator)
    return [single] if single is not None else None


def schema_sub_values(option_cls):
    """`{sub_key: {"values": [...]}}` for sub-settings whose Schema declares a vocabulary.

    Returns an empty dict for an option class that declares no Schema, which is the common case:
    nothing is emitted rather than something guessed.
    """
    if _schema_lib is None:
        return {}
    declared = getattr(option_cls, "schema", None)
    if not isinstance(declared, _schema_lib.Schema):
        return {}
    mapping = getattr(declared, "_schema", None)
    if not isinstance(mapping, dict):
        return {}

    out = {}
    for raw_key, validator in mapping.items():
        # Only a literal key names one sub-setting; `str` or a Regex as a key describes a family
        # of them, with no single sub-setting to attach values to.
        key = raw_key
        if isinstance(key, _schema_lib.Optional):
            key = getattr(key, "_schema", None)
        if not isinstance(key, str):
            continue

        values = values_from_validator(validator)
        if not values:
            continue
        deduped = list(dict.fromkeys(values))
        # One value is not a choice - the editor needs two before a dropdown beats a field.
        if len(deduped) < 2:
            continue
        out[key] = {"values": deduped}
    return out
