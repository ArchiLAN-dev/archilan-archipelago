"""Cap on the share of the others' progression a giant world can host (story 56.6).

Archipelago's fill shuffles the locations of every world together and puts each progression item on the first
reachable one, with no weighting by world. A world holding 40 % of the multiworld's locations therefore ends up
holding about 40 % of everyone's keys, and the game waits on whoever plays it.

Before the fill, a world whose locations able to receive progression exceed `k` times the fair share (one world's
worth: total / number of worlds) gets a seeded random selection of its still DEFAULT locations marked EXCLUDED, down
to `k` times the fair share. An excluded location only receives filler: the player keeps every check.

Guards: a world keeps room for its own local-only progression, and no more locations are excluded than there are
filler items to put on them (Fill raises otherwise). What a guard prevents is reported, never raised.

Off unless the generation sets ARCHILAN_PROGRESSION_CAP (the factor `k`, e.g. "2").
"""
from __future__ import annotations

import os
import random
import sys
from typing import Any, Callable

ENV_VAR = "ARCHILAN_PROGRESSION_CAP"


def cap_factor(environ: dict[str, str] | None = None) -> float | None:
    """The factor `k` asked by the generation, or None when the cap is off or the value is not a number above 1."""
    raw = (environ if environ is not None else os.environ).get(ENV_VAR, "").strip()
    if raw == "":
        return None
    try:
        value = float(raw)
    except ValueError:
        print(f"Warning: {ENV_VAR}={raw!r} is not a number, progression cap off", file=sys.stderr)
        return None
    return value if value > 1 else None


def capable_target(capable: dict[int, int], factor: float) -> dict[int, int]:
    """How many progression-capable locations each world may keep.

    Fixed point: excluding locations of a giant world lowers the total, hence the fair share, so the target is
    recomputed until it stops moving. With no more worlds than `k`, no world can exceed the cap.
    """
    worlds = len(capable)
    if worlds <= factor:
        return dict(capable)
    kept = dict(capable)
    for _ in range(50):
        limit = int(factor * sum(kept.values()) / worlds)
        updated = {world: min(capable[world], max(limit, 0)) for world in capable}
        if updated == kept:
            break
        kept = updated
    return kept


def plan_exclusions(
    capable_locations: dict[int, list[Any]],
    floors: dict[int, int],
    filler_budget: int,
    factor: float,
    seed: str,
) -> tuple[dict[int, list[Any]], list[str]]:
    """The locations to exclude per world, and what the guards held back.

    `capable_locations` are each world's unfilled DEFAULT locations, sorted. `floors` is the least each world must
    keep (its local-only progression). `filler_budget` is how many filler items remain for new excluded locations.
    """
    counts = {world: len(locations) for world, locations in capable_locations.items()}
    targets = capable_target(counts, factor)
    rng = random.Random(f"{seed}-archilan-progression-cap")
    plan: dict[int, list[Any]] = {}
    notes: list[str] = []
    budget = filler_budget

    for world in sorted(capable_locations):
        wanted = counts[world] - targets[world]
        if wanted <= 0:
            continue
        allowed = min(wanted, max(0, counts[world] - floors.get(world, 0)), max(0, budget))
        if allowed < wanted:
            notes.append(f"world {world}: excluded {allowed} of {wanted} locations"
                         f" (local progression {floors.get(world, 0)}, filler left {budget})")
        if allowed <= 0:
            continue
        plan[world] = rng.sample(capable_locations[world], allowed)
        budget -= allowed
    return plan, notes


def apply_progression_cap(multiworld: Any, factor: float, default_type: Any, excluded_type: Any) -> list[str]:
    """Mark the planned locations EXCLUDED on a multiworld about to be filled; returns the report lines."""
    players = list(multiworld.player_ids)
    capable: dict[int, list[Any]] = {p: [] for p in players}
    excluded_unfilled = 0
    for location in multiworld.get_unfilled_locations():
        if location.player not in capable:
            continue
        if location.progress_type == excluded_type:
            excluded_unfilled += 1
        elif location.progress_type == default_type:
            capable[location.player].append(location)
    # The Bridge observer and any world without a location to give do not count in the fair share.
    capable = {player: locations for player, locations in capable.items() if locations}
    for player in capable:
        capable[player].sort(key=lambda location: (location.address is None, location.address or 0, location.name))

    filler = sum(1 for item in multiworld.itempool if not item.advancement and not item.useful)
    floors: dict[int, int] = {}
    for player in players:
        local = getattr(getattr(multiworld.worlds[player], "options", None), "local_items", None)
        names = set(getattr(local, "value", ()) or ())
        floors[player] = sum(1 for item in multiworld.itempool
                             if item.player == player and item.advancement and item.name in names)

    plan, notes = plan_exclusions(capable, floors, filler - excluded_unfilled, factor, str(multiworld.seed))
    lines = list(notes)
    for player, locations in plan.items():
        for location in locations:
            location.progress_type = excluded_type
        lines.append(f"world {player} ({multiworld.player_name[player]}): {len(locations)} of"
                     f" {len(capable[player])} locations excluded from progression")
    return lines


def install(main_module: Any, factor: float) -> None:
    """Wrap Main's call to the fill so the cap applies right before it, after every world's pre_fill."""
    from BaseClasses import LocationProgressType

    original: Callable[..., Any] = main_module.distribute_items_restrictive

    def capped(multiworld: Any, *args: Any, **kwargs: Any) -> Any:
        for line in apply_progression_cap(multiworld, factor, LocationProgressType.DEFAULT,
                                          LocationProgressType.EXCLUDED):
            print(f"Progression cap: {line}", file=sys.stderr, flush=True)
        return original(multiworld, *args, **kwargs)

    main_module.distribute_items_restrictive = capped
