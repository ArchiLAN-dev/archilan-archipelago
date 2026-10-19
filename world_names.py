"""
The names a world declares statically, for the option editors' autocompletion (stories 4.14 and 9.58).

These are the World class's own tables, read without generating anything: options that create items or locations
at generation are not reflected, so consumers treat every list as a hint, never as a validation.

No import-time side effects: the image imports this module at build time to prove it is there (see Dockerfile).
"""
from __future__ import annotations

# Archipelago adds this group to every world (AutoWorldRegister): it holds every item, so it would only repeat the
# item list. Consumers know it by name.
EVERYTHING_GROUP = "Everything"


def _names(table) -> list[str]:
    try:
        return sorted({str(name) for name in (table or {})})
    except Exception:
        return []


def static_locations(world_cls) -> list[str]:
    """The world's location names: the keys of `location_name_to_id`."""
    return _names(getattr(world_cls, "location_name_to_id", None))


def static_items(world_cls) -> list[str]:
    """The world's item names: the keys of `item_name_to_id`."""
    return _names(getattr(world_cls, "item_name_to_id", None))


def item_groups(world_cls) -> dict[str, list[str]]:
    """
    The world's item groups (`item_name_groups`), each with its items sorted, `Everything` left out. A group that is
    not a collection of names, or is empty, is dropped rather than half read.
    """
    raw = getattr(world_cls, "item_name_groups", None)
    if not isinstance(raw, dict):
        return {}

    groups: dict[str, list[str]] = {}
    for name, members in raw.items():
        if str(name) == EVERYTHING_GROUP or isinstance(members, (str, bytes)):
            continue
        try:
            items = sorted({str(item) for item in members})
        except TypeError:
            continue
        if items:
            groups[str(name)] = items

    return dict(sorted(groups.items()))
