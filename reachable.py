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

from BaseClasses import CollectionState, LocationProgressType, MultiWorld, ItemClassification  # noqa: E402
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

# ---------------------------------------------------------------------------
# Exact regeneration from the generation seed
# ---------------------------------------------------------------------------
# AP's generation is deterministic: same yamls, same apworlds, same AP version and the same seed give
# the same multiworld, down to every roll (shop slots, prices, random-range options, world-internal
# draws). The seed is on the first line of the spoiler written next to the multidata. Rebuilding the
# whole multiworld with it reproduced a real 10-player run exactly, so when it is available the pass
# answers on the real world instead of a single player rebuilt with a fresh seed. A guard compares the
# rebuilt world with the multidata before trusting it: an AP upgrade or a re-uploaded apworld breaks
# the reproduction, and the pass then falls back on the single-player rebuild (build_multiworld).

_SEED_LINE = re.compile(r"Seed:\s*(\d+)")


def _read_generation_seed(arch_path: str) -> int | None:
    """The seed the multiworld was generated with, from the spoiler next to (or zipped with) the
    multidata. None for a seed generated without a spoiler (race) or imported from elsewhere."""
    path = Path(arch_path)
    head = ""
    try:
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as zf:
                name = next((n for n in zf.namelist() if n.endswith("_Spoiler.txt")), None)
                if name is not None:
                    head = zf.read(name)[:512].decode("utf-8-sig", errors="replace")
        else:
            spoiler = path.with_name(f"{path.stem}_Spoiler.txt")
            if spoiler.is_file():
                with open(spoiler, encoding="utf-8-sig", errors="replace") as handle:
                    head = handle.read(512)
    except (OSError, zipfile.BadZipFile):
        return None
    match = _SEED_LINE.search(head)
    return int(match.group(1)) if match else None


def _exact_mismatch(locations: dict, precollected: dict, arch: dict) -> str | None:
    """Why the rebuilt world is not the one the multidata describes, or None when it is.

    locations: slot -> set of location ids of the rebuilt world; precollected: slot -> list of the
    ids it precollected. Every slot of the multidata must match, not just the one asked about: a
    divergence anywhere means the reproduction is not the real generation."""
    arch_locations = arch.get("locations", {})
    for slot, slot_locations in arch_locations.items():
        if locations.get(slot) != set(slot_locations):
            return f"slot {slot}: locations differ"
    for slot, arch_items in arch.get("precollected_items", {}).items():
        if slot in arch_locations and list(precollected.get(slot, [])) != list(arch_items):
            return f"slot {slot}: starting inventory differs"
    return None


def build_exact_multiworld(yaml_dir: str, seed: int) -> MultiWorld:
    """Replay Main.main up to (not including) the fill, with the generation's own seed."""
    from Fill import parse_planned_blocks
    from Generate import main as GMain, mystery_argparse
    from Options import StartInventoryPool
    from worlds.generic.Rules import locality_rules

    sys.argv = [sys.argv[0]]
    args = mystery_argparse()
    args.player_files_path = yaml_dir
    args.seed = seed
    args.skip_output = True
    args.log_level = "error"
    g_args, g_seed = GMain(args)

    mw = MultiWorld(g_args.multi)
    mw.set_seed(g_seed, g_args.race, str(g_args.outputname) if g_args.outputname else None)
    mw.plando_options = g_args.plando
    mw.game = g_args.game.copy()
    mw.player_name = g_args.name.copy()
    mw.sprite = g_args.sprite.copy()
    mw.sprite_pool = g_args.sprite_pool.copy()
    mw.set_options(g_args)
    mw.set_item_links()
    mw.state = CollectionState(mw)

    AutoWorld.call_all(mw, "generate_early")

    # Same starting inventory handling as Main.main, in the same order: it creates items, and worlds
    # are free to draw from their random while doing so.
    for player in mw.player_ids:
        options = mw.worlds[player].options
        for item_name, count in options.start_inventory.value.items():
            for _ in range(count):
                mw.push_precollected(mw.create_item(item_name, player))
        for item_name, count in getattr(options, "start_inventory_from_pool", StartInventoryPool({})).value.items():
            for _ in range(count):
                mw.push_precollected(mw.create_item(item_name, player))
            early = mw.early_items[player].get(item_name, 0)
            if early:
                mw.early_items[player][item_name] = max(0, early - count)
                remaining_count = count - early
                if remaining_count > 0:
                    local_early = mw.local_early_items[player].get(item_name, 0)
                    if local_early:
                        mw.early_items[player][item_name] = max(0, local_early - remaining_count)
        options.non_local_items.value -= options.local_items.value
        options.non_local_items.value -= set(mw.local_early_items[player])
    if mw.players == 1:
        mw.worlds[1].options.non_local_items.value = set()
        mw.worlds[1].options.local_items.value = set()

    AutoWorld.call_all(mw, "create_regions")
    AutoWorld.call_all(mw, "create_items")
    AutoWorld.call_all(mw, "set_rules")

    for player in mw.player_ids:
        options = mw.worlds[player].options
        exclusion_rules(mw, player, options.exclude_locations.value)
        options.priority_locations.value -= options.exclude_locations.value
        for location_name in list(options.priority_locations.value):
            try:
                location = mw.get_location(location_name, player)
            except KeyError:
                continue
            if location.progress_type != LocationProgressType.EXCLUDED:
                location.progress_type = LocationProgressType.PRIORITY
            else:
                options.priority_locations.value.discard(location_name)
    if mw.players > 1:
        locality_rules(mw)
    mw.plando_item_blocks = parse_planned_blocks(mw)

    AutoWorld.call_all(mw, "connect_entrances")
    AutoWorld.call_all(mw, "generate_basic")
    return mw


def _load_exact_multiworld(arch_path: str, yaml_dir: str, arch: dict) -> MultiWorld | None:
    """The real multiworld, rebuilt and checked against the multidata, or None to fall back."""
    seed = _read_generation_seed(arch_path)
    if seed is None:
        print("exact regeneration: no seed (no spoiler), falling back", file=sys.stderr)
        return None
    try:
        mw = build_exact_multiworld(yaml_dir, seed)
    except Exception as exc:
        print(f"exact regeneration failed, falling back: {exc}", file=sys.stderr)
        return None
    locations = {
        player: {loc.address for loc in mw.get_locations(player) if isinstance(loc.address, int)}
        for player in mw.player_ids
    }
    precollected = {
        player: [item.code for item in mw.precollected_items[player] if type(item.code) == int]
        for player in mw.player_ids
    }
    mismatch = _exact_mismatch(locations, precollected, arch)
    if mismatch is not None:
        print(f"exact regeneration diverges from the multidata ({mismatch}), falling back", file=sys.stderr)
        return None
    return mw


# ---------------------------------------------------------------------------
# Replaying a world's own rolls from its slot_data
# ---------------------------------------------------------------------------
# The regeneration below rolls everything again with a fresh seed. A UT-aware world gets the seed's
# values back through interpret_slot_data / re_gen_passthrough; a world without that hook but whose
# fill_slot_data writes its rolls out can still be replayed here, stage by stage, at the point where
# the world reads each value.

def _hk_replay(stage: str, world, slot_data: dict) -> None:
    """Hollow Knight rolls its shop slot counts (options drawn from a random range), the price of
    every shop location and the charm notch costs - all of which logic reads. fill_slot_data writes
    them back as `options`, `location_costs`, `notch_costs` and `grub_count`."""
    if stage == "before_generate_early":
        # Shop slot counts decide which shop locations exist; create_items reads them.
        for name, value in (slot_data.get("options") or {}).items():
            option = getattr(world.options, name, None)
            if option is not None:
                option.value = value
    elif stage == "after_generate_early":
        # generate_early rolls both; logic reads them afterwards.
        if slot_data.get("notch_costs"):
            world.charm_costs = list(slot_data["notch_costs"])
        if "grub_count" in slot_data:
            world.grub_count = slot_data["grub_count"]
            world.grub_player_count = {world.player: slot_data["grub_count"]}
    elif stage == "after_create_items":
        # create_items spreads ExtraShopSlots over the shops at random, so even with the seed's slot
        # options each shop ends up with its own count. Every shop location has a price, so the
        # seed's location_costs says how many each shop really has: trim the extra slots (the last
        # ones created) and create the missing ones, which HK numbers in sequence.
        costs = slot_data.get("location_costs") or {}
        if costs:
            region = world.multiworld.get_region("Menu", world.player)
            for shop, shop_locations in world.created_multi_locations.items():
                wanted = sum(
                    1 for name in costs
                    if name.rsplit("_", 1)[0] == shop and name.rsplit("_", 1)[-1].isdigit()
                )
                while len(shop_locations) > wanted:
                    region.locations.remove(shop_locations.pop())
                while len(shop_locations) < wanted:
                    world.create_location(shop)
    elif stage == "before_set_rules":
        # set_rules captures each cost in a closure: the seed's prices must be in place first.
        costs = slot_data.get("location_costs") or {}
        for location in world.multiworld.get_locations(world.player):
            if location.name in costs:
                location.costs = dict(costs[location.name])


_SLOT_DATA_REPLAY = {"Hollow Knight": _hk_replay}


def _replay_slot_data(stage: str, world, slot_data: dict) -> None:
    replay = _SLOT_DATA_REPLAY.get(world.game)
    if replay is not None and slot_data:
        replay(stage, world, slot_data)

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
    world = mw.worlds[1]

    _replay_slot_data("before_generate_early", world, slot_data)
    for step in gen_steps:
        if step == "set_rules":
            _replay_slot_data("before_set_rules", world, slot_data)
        AutoWorld.call_all(mw, step)
        if step == "generate_early":
            _replay_slot_data("after_generate_early", world, slot_data)
        if step == "create_items":
            _replay_slot_data("after_create_items", world, slot_data)
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

    Except events. Main.py only serializes items with an int id, so a precollected event never reaches
    the multidata - and some worlds start from one: Hollow Knight pushes its start location
    (`Tutorial_01`) as an event in generate_early. Dropping it left the player with no start region and
    nothing ever in logic. Events are the world's own bookkeeping, not a random roll, so the ones the
    regeneration pushed are kept.
    """
    precollected_ids = arch.get("precollected_items", {}).get(slot, [])

    events = [item for item in mw.precollected_items.get(player_id, []) if getattr(item, "code", None) is None]

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

    mw.precollected_items[player_id] = events + items


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

    exact_mw = _load_exact_multiworld(args.archipelago, str(Path(yaml_path).parent), arch)
    try:
        if exact_mw is not None:
            # The real world: slot numbers are the generation's, the starting inventory is the one
            # handed out, every roll is the seed's.
            mw, player_id = exact_mw, slot
        else:
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
    if exact_mw is None:
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
