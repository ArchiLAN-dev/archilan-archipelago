#!/usr/bin/env python3
"""Headless reachability checker using AP's logic engine.

Usage:
    python reachable.py \
        --archipelago /path/to/AP_xxx.archipelago \
        --yamls      /path/to/yamls/ \
        --apsave     /path/to/AP_xxx.apsave \
        --slot       1

Outputs JSON to stdout:
{
  "reachable":  [{"id": 123, "name": "..."}],
  "checked":    [{"id": 123, "name": "..."}],
  "unreachable": [{"id": 123, "name": "..."}],
  "items_received": [{"id": 123, "name": "...", "count": 2}]
}
"""
from __future__ import annotations

import argparse
import atexit
import importlib.abc
import importlib.machinery
import json
import json as _json
import logging
import pathlib
import pickle
import re
import shutil
import sys
import tempfile
import types
import warnings
import zipfile
import zlib
import glob
import os
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------------------
# Protocol stdout isolation (must run before any AP/apworld import)
# ---------------------------------------------------------------------------
# This script speaks a strict newline-delimited JSON protocol on stdout: in --daemon mode the
# bridge reads exactly one JSON line per message (ready, then one result per request). AP core
# and third-party apworlds print() freely to stdout during world generation (e.g. the Simpsons
# Hit and Run apworld prints "Getting UT slot data."), which would corrupt that protocol. The
# protocol_io module reserves the real stdout for emit()-ed protocol lines and routes every
# other write to stderr, which the bridge's frame demux ignores. See tests/test_protocol_io.py.
from protocol_io import emit as _emit, isolate_stdout  # noqa: E402

isolate_stdout()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

AP_SRC = "/app/ArchipelagoSrc"
OFFICIAL_APWORLDS = pathlib.Path("/app/Archipelago/Archipelago/lib/worlds")

# Apworld search paths derived from ARCHIPELAGO_OUTPUT_DIR.
# Structure: /workspace/{sessionId}/output  →  /workspace/{sessionId}/apworlds  (session-specific)
#                                           →  /workspace/apworlds               (shared workspace pool)
# Fallback for legacy bind-mount setups where no env var is set:
#   /archipelago/output  →  /apworlds (runner copies per-session apworlds there)
_OUTPUT_DIR_ENV = os.environ.get("ARCHIPELAGO_OUTPUT_DIR", "/archipelago/output")
_SESSION_DIR = pathlib.Path(os.path.dirname(_OUTPUT_DIR_ENV))
_WORKSPACE_DIR = pathlib.Path(os.path.dirname(str(_SESSION_DIR)))
APWORLDS_SESSION = _SESSION_DIR / "apworlds"          # session-specific custom worlds
APWORLDS_POOL = _WORKSPACE_DIR / "apworlds"           # shared workspace pool
APWORLDS_IN = pathlib.Path("/apworlds")               # legacy: bind-mount per-session copy
APWORLDS_DEV = pathlib.Path("/arch_workspace/apworlds")  # dev: workspace volume bind-mount

if AP_SRC not in sys.path:
    sys.path.insert(0, AP_SRC)

# ---------------------------------------------------------------------------
# Pre-stubs (must run before any AP import)
# ---------------------------------------------------------------------------

_mu = types.ModuleType("ModuleUpdate")
_mu.update = lambda *_, **__: None  # type: ignore[attr-defined]
sys.modules["ModuleUpdate"] = _mu

_winapi_stub = types.ModuleType("_winapi")
def _winapi_getattr(name):
    """Answer anything with 0 - except the dunders, which must stay absent.

    `inspect.getmodule` walks `sys.modules`, keeps every module that `hasattr(m, "__file__")`, and
    calls `inspect.getabsfile` on it without a guard. A stub that answers `0` to `__file__` therefore
    passes the check and then raises `TypeError: <module '_winapi' from 0> is a built-in module`,
    breaking any code that inspects the call stack. gtfo does exactly that, through
    `importlib.resources.files()`.

    Raising AttributeError for dunders makes the stub look like the built-in module it stands in for,
    which `getmodule` skips.
    """
    if name.startswith("__") and name.endswith("__"):
        raise AttributeError(name)
    return 0


_winapi_stub.__getattr__ = _winapi_getattr  # type: ignore[method-assign]
sys.modules["_winapi"] = _winapi_stub

# orjson: prefer the real library when the image carries it, and fall back to a json-backed stub.
# The `.orjson` attribute matters - the real package exposes its native extension as a submodule of
# that name, and a world written against it does `from orjson import orjson`.
#
# Kept identical to the three other scripts. This one was the last to still carry the bare stub, and
# it broke the reachability daemon outright for any session holding a Clair Obscur yaml.
try:
    import orjson as _orjson  # noqa: F401
except ImportError:
    _orjson = types.ModuleType("orjson")
    _orjson.loads = _json.loads  # type: ignore[attr-defined]
    _orjson.dumps = lambda obj, **kw: _json.dumps(obj, default=str).encode()  # type: ignore[attr-defined]
    sys.modules["orjson"] = _orjson
if not hasattr(_orjson, "orjson"):
    _orjson.orjson = _orjson  # type: ignore[attr-defined]

# tkinter / _tkinter: GUI toolkit not available in headless containers. The image ships the
# _tkinter extension but not libtk8.6.so, so importing it raises rather than being absent.
# Mirrors generate_multiworld.py: a world whose client UI imports tkinter at module level
# (e.g. minecraft_dig) must load here exactly as it does for generation, or the seed
# generates fine and its reachability then dies with "No world found to handle game X".
_tk_stub = types.ModuleType("tkinter")
_tk_stub.__getattr__ = lambda _n: _tk_stub  # type: ignore[attr-defined]
for _tk_name in ("tkinter", "_tkinter", "tkinter.ttk", "tkinter.font",
                 "tkinter.messagebox", "tkinter.filedialog", "tkinter.colorchooser",
                 "tkinter.simpledialog", "tkinter.constants"):
    sys.modules.setdefault(_tk_name, _tk_stub)

# pkg_resources: setuptools 71+ no longer ships it as a standalone top-level package. Pre-populate
# sys.modules from pip's vendored copy so apworlds that call pkg_resources.resource_listdir()
# (e.g. pokemon_emerald, to enumerate its data/regions/*.json) get the real implementation. With a
# stub instead, resource_listdir() yields zero region files and the world crashes at import with
# KeyError: 'POKEDEX_REWARD_001'. Mirrors generate_template.py / introspect_options.py.
try:
    import pkg_resources  # noqa: F401 - real implementation when setuptools < 71
except ImportError:
    from pip._vendor import pkg_resources as _pr  # type: ignore[no-redef]
    sys.modules["pkg_resources"] = _pr

# ---------------------------------------------------------------------------
# World imports (mirrors generate_multiworld.py)
# ---------------------------------------------------------------------------

# Honest first, stub only what is truly missing - see apworld_import.py. Keeping the same
# loader as the generator matters: a world that loads for generation must load here too,
# or its reachability goes silently missing.
from apworld_import import import_world  # noqa: E402

# ---------------------------------------------------------------------------
# AP imports (after stubs and sys.path setup)
# ---------------------------------------------------------------------------

warnings.filterwarnings("ignore")  # silence _speedups warning

from BaseClasses import CollectionState, MultiWorld, ItemClassification  # noqa: E402
from worlds import AutoWorld  # noqa: E402
import worlds as _worlds_pkg  # noqa: E402
from worlds.generic.Rules import exclusion_rules  # noqa: E402
from NetUtils import NetworkItem  # noqa: E402

logging.basicConfig(level=logging.ERROR)  # suppress AP generator noise

# ---------------------------------------------------------------------------
# Apworld loading (mirrors generate_multiworld.py)
# ---------------------------------------------------------------------------

def _sanitize_pkg_name(name: str) -> str:
    """A Python-importable name for an apworld folder that is not one (e.g. "Twilight Princess")."""
    sanitized = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    return sanitized


def _detect_pkg(entries: list[str]) -> tuple[str, str] | None:
    """The apworld's package folder, and the archive directory that holds it.

    Returns `(package name, path of its parent relative to the archive root)`.

    Kept identical to introspect_options.py. Only an `__init__.py` exactly one level down used to
    count, and the archive root was assumed to be the directory to expose; fez, dungeon_clawler and
    nrftw nest their package one level deeper, so the name came out right and the path did not.
    """
    best: tuple[int, list[str], str] | None = None
    for entry in entries:
        parts = entry.replace("\\", "/").split("/")
        if len(parts) >= 2 and parts[-1] == "__init__.py" and parts[-2]:
            if best is None or len(parts) < best[0]:
                best = (len(parts), parts[:-2], parts[-2])

    if best is not None:
        return best[2], "/".join(best[1])

    # No __init__.py anywhere: keep the old guess rather than skipping the archive outright.
    for entry in entries:
        root = entry.replace("\\", "/").split("/")[0]
        if root:
            return root, ""
    return None


def _load_apworlds_from(apworld_dir: pathlib.Path) -> None:
    if not apworld_dir.is_dir():
        return
    for apw in sorted(apworld_dir.glob("*.apworld")):
        try:
            with zipfile.ZipFile(str(apw)) as zf:
                entries = zf.namelist()
            detected = _detect_pkg(entries)
        except Exception as e:
            print(f"Warning: could not inspect {apw.name}: {e}", file=sys.stderr)
            continue
        if detected is None:
            print(f"Warning: skipping {apw.name}: could not detect package name", file=sys.stderr)
            continue

        raw_pkg, pkg_parent = detected

        # An apworld may ship its sources under a display-name folder that is not a valid Python
        # identifier ("Twilight Princess"). Such a package cannot be zipimported under that name, so
        # extract it and rename the folder - exactly what generate_multiworld.py does. Skipping it
        # instead (the previous behaviour) silently left the world unregistered, and the reachability
        # pass then died with "No world found to handle game Twilight Princess" while generation,
        # which does sanitize, worked fine (issue #278).
        pkg = raw_pkg if raw_pkg.isidentifier() else _sanitize_pkg_name(raw_pkg)
        if not pkg or not pkg.isidentifier():
            print(f"Warning: skipping {apw.name}: invalid package name '{raw_pkg}'", file=sys.stderr)
            continue
        mod = f"worlds.{pkg}"
        if mod in sys.modules:
            continue

        tmp_dir = tempfile.mkdtemp(prefix="apworld_")
        atexit.register(shutil.rmtree, tmp_dir, True)
        try:
            with zipfile.ZipFile(str(apw)) as zf:
                for member in zf.infolist():
                    member.filename = member.filename.replace("\\", "/")
                    zf.extract(member, tmp_dir)
            # What holds the package is the archive root only when the package sits directly
            # under it.
            pkg_root = os.path.join(tmp_dir, *pkg_parent.split("/")) if pkg_parent else tmp_dir

            if raw_pkg != pkg:
                raw_dir = os.path.join(pkg_root, raw_pkg)
                if os.path.isdir(raw_dir):
                    os.rename(raw_dir, os.path.join(pkg_root, pkg))
            # Bundled top-level deps sit at the zip root, so expose it on sys.path too.
            sys.path.insert(0, tmp_dir)
            _worlds_pkg.__path__.append(pkg_root)
            import_world(
                mod,
                on_stub=lambda name, a=apw.name: print(
                    f"Note: stubbed missing module '{name}' for {a}", file=sys.stderr),
            )
        except Exception as e:
            for _p in {pkg_root, tmp_dir}:
                if _p in _worlds_pkg.__path__:
                    _worlds_pkg.__path__.remove(_p)
            if tmp_dir in sys.path:
                sys.path.remove(tmp_dir)
            print(f"Warning: failed to load {apw.name} ({pkg}): {e}", file=sys.stderr)


# Load official apworlds, then custom apworlds (dev + prod + session-specific).
_load_apworlds_from(OFFICIAL_APWORLDS)
_load_apworlds_from(APWORLDS_DEV)
_load_apworlds_from(APWORLDS_IN)
_load_apworlds_from(APWORLDS_POOL)
_load_apworlds_from(APWORLDS_SESSION)
# AP_WORLDS_DIR overrides the derived session path (set by bridge docker runtime adapter)
_AP_WORLDS_DIR_ENV = os.environ.get("AP_WORLDS_DIR")
if _AP_WORLDS_DIR_ENV:
    _load_apworlds_from(pathlib.Path(_AP_WORLDS_DIR_ENV))

# Rebuild network_data_package to include late-loaded worlds
_worlds_pkg.network_data_package["games"].update({
    cls.game: cls.get_data_package_data()
    for cls in _worlds_pkg.AutoWorldRegister.world_types.values()
})

# ---------------------------------------------------------------------------
# Save helpers (same as bridge)
# ---------------------------------------------------------------------------

def _slot_map(mapping: dict) -> dict:
    """Normalize AP save keys: (team, slot[, remote_items]) tuples to a slot int (team 0 only).

    `received_items` uses 3-element keys (team, slot, remote_items). AP appends every item a
    slot receives to the `remote_items=True` list, and only the items coming from *other*
    players to the `False` one (MultiServer.send_items_to), so `True` is a superset of
    `False`, never a disjoint half. Concatenating the two counted every item twice, which
    inflated count-based logic in the reachability pass (`state.has(item, player, n)`: a
    single Progressive Bow read as two, unlocking fire/ice arrows) and doubled the reported
    "items received". Keep the `True` list; fall back to `False` when it is missing.
    """
    result: dict = {}
    for key, val in mapping.items():
        if isinstance(key, int):
            result[key] = val
            continue
        if not (isinstance(key, tuple) and len(key) >= 2 and key[0] == 0):
            continue
        slot = int(key[1])
        if len(key) >= 3:
            # (team, slot, remote_items): the remote_items=True list wins, whatever the order.
            if key[2] or slot not in result:
                result[slot] = val
            continue
        existing = result.get(slot)
        if isinstance(existing, (set, frozenset)) and isinstance(val, (set, frozenset)):
            result[slot] = existing | val
        else:
            result[slot] = val
    return result


def load_apsave(path: str) -> dict:
    with open(path, "rb") as f:
        return pickle.loads(zlib.decompress(f.read()))


def load_archipelago(path: str) -> dict:
    with open(path, "rb") as f:
        data = f.read()
    if path.endswith(".zip"):
        with zipfile.ZipFile(__import__("io").BytesIO(data)) as zf:
            arch_name = next((n for n in zf.namelist() if n.endswith(".archipelago")), None)
            data = zf.read(arch_name if arch_name else zf.namelist()[0])
    return pickle.loads(zlib.decompress(data[1:]))


# ---------------------------------------------------------------------------
# Fake AP generation
# ---------------------------------------------------------------------------

def build_multiworld(game: str, player_name: str, yaml_path: str, slot_data: dict) -> tuple[MultiWorld, int]:
    """Regenerate a minimal MultiWorld (rules only, no item placement)."""
    from Generate import main as GMain, mystery_argparse

    sys.argv = [sys.argv[0]]
    args = mystery_argparse()
    args.player_files_path = str(Path(yaml_path).parent)
    args.skip_output = True
    args.multi = 0
    args.log_level = "error"

    g_args, seed = GMain(args)

    # Find our slot in the generated args
    player_id = next((p for p, n in g_args.name.items() if n == player_name), 1)

    g_args.multi = 1
    g_args.game = {1: game}
    g_args.name = {1: player_name}
    g_args.player_ids = {1}

    # Copy the player's options onto slot 1
    for attr in vars(g_args):
        val = getattr(g_args, attr)
        if isinstance(val, dict) and player_id in val and player_id != 1:
            val[1] = val[player_id]

    gen_steps = [s for s in (
        "generate_early", "create_regions", "create_items",
        "set_rules", "connect_entrances", "generate_basic",
    ) if hasattr(AutoWorld.World, s)]

    mw = MultiWorld(1)
    mw.generation_is_fake = True
    # Universal Tracker does not expose the raw network slot_data as re_gen_passthrough:
    # it first runs the world's interpret_slot_data() hook, which rebuilds structures the
    # network serialization flattened (e.g. Mirror's Edge stores target_times keyed by the
    # MirrorsEdgeLevels enum, but fill_slot_data serializes those keys as plain strings).
    # generate_early() of such worlds then assumes the enum keys are back. Our fake-gen
    # harness must emulate UT here too; otherwise interpret-dependent worlds crash during
    # the reachability pass (AttributeError: 'str' object has no attribute 'value').
    passthrough = slot_data
    if slot_data:
        world_cls = AutoWorld.AutoWorldRegister.world_types.get(game)
        interpret = getattr(world_cls, "interpret_slot_data", None) if world_cls else None
        if interpret is not None:
            try:
                # process_slot_data mutates in place, so hand it a defensive copy.
                result = interpret(dict(slot_data))
                if result:  # UT only overrides re_gen_passthrough when interpret returns truthy.
                    passthrough = result
            except Exception:
                pass  # fall back to the raw slot_data
    mw.re_gen_passthrough = {game: passthrough} if passthrough else {}
    # Universal Tracker sets this attribute on the multiworld; UT-aware worlds (e.g. pokepark)
    # read it when generation_is_fake is True. Our fake-gen harness must emulate UT here too:
    # default to "off" (no deferred-connection enforcement) so those worlds don't crash on a
    # missing attribute during a static reachability pass.
    mw.enforce_deferred_connections = "off"
    mw.set_seed(seed, g_args.race, str(g_args.outputname) if g_args.outputname else None)
    mw.game = {1: game}
    mw.player_name = {1: player_name}
    mw.set_options(g_args)
    mw.state = CollectionState(mw)

    for step in gen_steps:
        AutoWorld.call_all(mw, step)
        if step == "set_rules":
            exclusion_rules(mw, 1, mw.worlds[1].options.exclude_locations.value)
        if step == "generate_basic":
            break

    # mw.precollected_items[1] is left as create_items() filled it, and main() then replaces it
    # with the seed's own starting inventory - see _seed_precollected_items. Starting items are
    # precollected at generation time and are NOT sent via the AP received_items protocol, so
    # dropping them entirely would lose them. CollectionState(mw) auto-collects whatever is in
    # there with event=False (updates reachable_regions); received_items are collected on top -
    # double-collecting a progression item is harmless for boolean has() checks.

    return mw, 1


def _seed_precollected_items(mw, player_id, arch, slot, item_id_to_name) -> None:
    """Replace the regenerated starting inventory with the one the seed actually handed out.

    push_precollected() runs during create_items, and a world is free to draw its starting items
    at random: Sayonara Wild Hearts picks the level you begin with via world.random.choice. Our
    fake generation rolls its own seed, so it hands out a *different* start on every run - three
    consecutive reachability passes on the same save answered Laser Love, Hate Skulls and Forest
    Dub. Reachability was effectively random for those worlds, wrong in both directions: it opened
    a level the player never got and hid the one they did.

    The multidata records what was really precollected (Main.py serializes
    multiworld.precollected_items), so it is the authority. Replace rather than merge: anything the
    regeneration rolled is by definition not what the player started with.
    """
    precollected_ids = arch.get("precollected_items", {}).get(slot, [])

    items = []
    for item_id in precollected_ids:
        name = item_id_to_name.get(item_id)
        if name is None:
            print(f"Warning: precollected item #{item_id} is not in the datapackage, skipped",
                  file=sys.stderr)
            continue
        try:
            items.append(mw.create_item(name, player_id))
        except Exception as exc:
            # A world updated since the seed was rolled may no longer know the name. Losing one
            # starting item skews the answer; taking down the whole pass would remove it entirely.
            print(f"Warning: could not recreate precollected item '{name}': {exc}", file=sys.stderr)

    mw.precollected_items[player_id] = items


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archipelago", required=True)
    parser.add_argument("--yamls", required=True)
    parser.add_argument("--apsave", required=False, default=None)
    parser.add_argument("--slot", type=int, default=1)
    parser.add_argument(
        "--daemon", action="store_true",
        help="Persistent mode: read JSON requests from stdin, write JSON results to stdout",
    )
    args = parser.parse_args()

    # ── One-time setup (expensive) ────────────────────────────────────────────

    arch = load_archipelago(args.archipelago)

    slot_info = arch["slot_info"]
    slot = args.slot
    net_slot = slot_info.get(slot)
    if net_slot is None:
        _emit({"error": f"slot {slot} not found"})
        sys.exit(1)

    game: str = net_slot.game
    player_name: str = net_slot.name
    slot_data: dict = arch.get("slot_data", {}).get(slot, {})

    dp = arch.get("datapackage", {}).get(game, {})
    id_to_loc = {v: k for k, v in dp.get("location_name_to_id", {}).items()}
    id_to_item: dict[int, str] = {}
    for _gdata in arch.get("datapackage", {}).values():
        for _iname, _iid in _gdata.get("item_name_to_id", {}).items():
            id_to_item[_iid] = _iname
    slot_names: dict[int, str] = {s: ns.name for s, ns in slot_info.items()}
    arch_locs: dict[int, tuple] = arch.get("locations", {}).get(slot, {})

    # Items expected for this slot - static, computed once from seed
    expected_counter: Counter = Counter()
    for _slot_locs in arch.get("locations", {}).values():
        for _item_id, _recv_slot, _flags in _slot_locs.values():
            if _recv_slot == slot and _item_id > 0:
                expected_counter[id_to_item.get(_item_id, f"#{_item_id}")] += 1

    raw_spheres = arch.get("spheres", [])

    yaml_candidates = list(Path(args.yamls).glob(f"{player_name}.yaml"))
    if not yaml_candidates:
        yaml_candidates = list(Path(args.yamls).glob("*.yaml"))
    if not yaml_candidates:
        _emit({"error": f"no yaml found in {args.yamls}"})
        sys.exit(1)
    yaml_path = str(yaml_candidates[0])

    try:
        mw, player_id = build_multiworld(game, player_name, yaml_path, slot_data)
    except Exception as exc:
        # A single world that fails to fake-generate (e.g. a buggy apworld whose generate_early
        # raises) must not take down the whole daemon and surface to the bridge as an opaque
        # "reachable daemon stream closed". Emit a structured error on stdout instead: in daemon
        # mode the bridge reads it as a non-ready line and reports it; in one-shot mode the bridge
        # extracts {"error": ...} from stdout. Either way the other slots keep working.
        _emit({"error": f"reachability generation failed for {game}: {exc}"})
        sys.exit(1)
    # Prefer the session's own datapackage for ID→name resolution: it matches the IDs
    # in received_items exactly (same generation). The rebuilt world's item_id_to_name
    # can diverge if the apworld was updated after the session was created.
    _arch_id_to_name: dict[int, str] = {
        v: k for k, v in dp.get("item_name_to_id", {}).items()
    }
    _world_id_to_name: dict[int, str] = mw.worlds[player_id].item_id_to_name
    item_id_to_name: dict[int, str] = {**_world_id_to_name, **_arch_id_to_name}
    _seed_precollected_items(mw, player_id, arch, slot, item_id_to_name)
    event_locations = [loc for loc in mw.get_locations(player_id) if not loc.address]

    # ── Per-request computation (fast once multiworld is loaded) ──────────────

    def _compute(checked_ids: set[int], received_items: list) -> dict:
        """Compute reachability from in-memory state.

        checked_ids: set of checked location IDs for this slot.
        received_items: list of [item_id, sender_slot, location_id] tuples/lists.
        """
        missing_ids = set(arch_locs.keys()) - checked_ids

        cs = CollectionState(mw)
        item_counts: Counter = Counter()
        for entry in received_items:
            item_id = entry[0] if isinstance(entry, (list, tuple)) else (entry.item if hasattr(entry, "item") else 0)
            if item_id <= 0 or item_id not in item_id_to_name:
                continue
            name = item_id_to_name[item_id]
            world_item = mw.create_item(name, player_id)
            cs.collect(world_item)
            item_counts[name] += 1

        cs.sweep_for_advancements(locations=event_locations)

        reachable_ids: set[int] = {
            loc.address
            for loc in mw.get_reachable_locations(cs, player_id)
            if loc.address is not None and not isinstance(loc.address, list)
        }

        def loc_entry(loc_id: int) -> dict:
            name = id_to_loc.get(loc_id, f"#{loc_id}")
            item_id_l, recv_slot, flags = arch_locs.get(loc_id, (0, slot, 0))
            return {
                "id": loc_id,
                "name": name,
                "item": {
                    "id": item_id_l,
                    "name": id_to_item.get(item_id_l, f"#{item_id_l}"),
                    "flags": flags,
                    "slot": recv_slot,
                    "slot_name": slot_names.get(recv_slot, f"Slot {recv_slot}"),
                },
            }

        reachable_unchecked = [loc_entry(i) for i in reachable_ids if i in missing_ids]
        reachable_checked   = [loc_entry(i) for i in reachable_ids if i in checked_ids]
        unreachable         = [loc_entry(i) for i in missing_ids if i not in reachable_ids]
        checked_not_reach   = [loc_entry(i) for i in checked_ids if i not in reachable_ids]

        items_out = [
            {"id": dp.get("item_name_to_id", {}).get(name, 0), "name": name, "count": count}
            for name, count in item_counts.most_common()
        ]

        not_received_counter = expected_counter - item_counts
        items_not_received_out = [
            {"id": dp.get("item_name_to_id", {}).get(name, 0), "name": name, "count": count}
            for name, count in not_received_counter.most_common()
        ]

        def sphere_loc_entry(loc_id: int) -> dict:
            entry = loc_entry(loc_id)
            if loc_id in checked_ids:
                entry["check_status"] = "checked"
            elif loc_id in reachable_ids:
                entry["check_status"] = "reachable"
            else:
                entry["check_status"] = "blocked"
            return entry

        spheres_out = []
        for _i, _sphere in enumerate(raw_spheres):
            _ids = sorted(_sphere.get(slot, set()))
            if not _ids:
                continue
            _s_checked = [_l for _l in _ids if _l in checked_ids]
            _s_reach   = [_l for _l in _ids if _l in reachable_ids and _l not in checked_ids]
            _s_future  = [_l for _l in _ids if _l not in checked_ids and _l not in reachable_ids]
            if len(_s_checked) == len(_ids):
                _status = "past"
            elif _s_reach:
                _status = "current"
            else:
                _status = "future"
            spheres_out.append({
                "index": _i,
                "status": _status,
                "counts": {
                    "total": len(_ids),
                    "checked": len(_s_checked),
                    "reachable": len(_s_reach),
                    "blocked": len(_s_future),
                },
                "locations": [sphere_loc_entry(_l) for _l in _ids],
            })

        return {
            "game": game,
            "player": player_name,
            "reachable_unchecked": reachable_unchecked,
            "reachable_checked": reachable_checked,
            "unreachable_unchecked": unreachable,
            "checked_unreachable": checked_not_reach,
            "items_received": items_out,
            "items_not_received": items_not_received_out,
            "spheres": spheres_out,
            "counts": {
                "checked": len(checked_ids),
                "total": len(arch_locs),
                "reachable_now": len(reachable_unchecked),
            },
        }

    # ── Run mode ──────────────────────────────────────────────────────────────

    if args.daemon:
        # Signal readiness, then serve requests from stdin indefinitely.
        # Request: {"checked_locations": [...], "received_items": [[id,sender,loc], ...]}\n
        # Response: {result JSON}\n
        _emit({"ready": True})
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                checked = set(req.get("checked_locations", []))
                ri = req.get("received_items", [])
                result = _compute(checked, ri)
                _emit(result)
            except Exception as exc:
                _emit({"error": str(exc)})
    else:
        # One-shot mode: read state from env var, stdin, or fall back to --apsave.
        checked_ids: set[int] = set()
        received_items: list = []
        state_from_stdin = False
        state_env = os.environ.get("REACHABLE_STATE_JSON")
        if state_env:
            try:
                req = json.loads(state_env)
                checked_ids = set(req.get("checked_locations", []))
                received_items = req.get("received_items", [])
                state_from_stdin = True
            except (json.JSONDecodeError, Exception):
                pass
        if not state_from_stdin and not sys.stdin.isatty():
            line = sys.stdin.readline().strip()
            if line:
                try:
                    req = json.loads(line)
                    checked_ids = set(req.get("checked_locations", []))
                    received_items = req.get("received_items", [])
                    state_from_stdin = True
                except (json.JSONDecodeError, Exception):
                    pass
        if not state_from_stdin and args.apsave and os.path.isfile(args.apsave):
            save = load_apsave(args.apsave)
            loc_checks = _slot_map(save.get("location_checks", {}))
            checked_ids = set(loc_checks.get(slot, set()))
            ri_map = _slot_map(save.get("received_items", {}))
            received_items = ri_map.get(slot, [])
        _emit(_compute(checked_ids, received_items))


if __name__ == "__main__":
    main()
