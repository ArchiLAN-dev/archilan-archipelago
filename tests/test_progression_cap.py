"""Cap on the share of the others' progression a giant world hosts (story 56.6)."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

import progression_cap  # noqa: E402

DEFAULT, EXCLUDED = "default", "excluded"


def locations(world: int, count: int) -> list[SimpleNamespace]:
    return [SimpleNamespace(player=world, address=world * 10000 + i, name=f"L{world}-{i}", progress_type=DEFAULT)
            for i in range(count)]


def test_the_cap_is_off_unless_asked() -> None:
    assert progression_cap.cap_factor({}) is None
    assert progression_cap.cap_factor({progression_cap.ENV_VAR: ""}) is None
    assert progression_cap.cap_factor({progression_cap.ENV_VAR: "nope"}) is None
    assert progression_cap.cap_factor({progression_cap.ENV_VAR: "1"}) is None
    assert progression_cap.cap_factor({progression_cap.ENV_VAR: "2"}) == 2.0


def test_only_the_giant_world_is_brought_down_to_k_times_the_fair_share() -> None:
    # 100 + 100 + 100 + 900 over 4 worlds: the giant keeps 2 x (300 + kept) / 4, i.e. 300.
    targets = progression_cap.capable_target({1: 100, 2: 100, 3: 100, 4: 900}, 2.0)

    assert targets[1] == targets[2] == targets[3] == 100
    assert targets[4] == 300
    assert targets[4] <= 2 * sum(targets.values()) / 4


def test_with_no_more_worlds_than_k_nothing_is_capped() -> None:
    assert progression_cap.capable_target({1: 10, 2: 990}, 2.0) == {1: 10, 2: 990}


def test_the_selection_is_the_same_for_the_same_seed_and_differs_for_another() -> None:
    capable = {1: locations(1, 50), 2: locations(2, 50), 3: locations(3, 400)}

    first, _ = progression_cap.plan_exclusions(capable, {}, 10_000, 2.0, "seed-a")
    again, _ = progression_cap.plan_exclusions(capable, {}, 10_000, 2.0, "seed-a")
    other, _ = progression_cap.plan_exclusions(capable, {}, 10_000, 2.0, "seed-b")

    assert set(first) == {3}
    assert [loc.name for loc in first[3]] == [loc.name for loc in again[3]]
    assert [loc.name for loc in first[3]] != [loc.name for loc in other[3]]


def test_a_world_keeps_room_for_its_local_progression() -> None:
    capable = {1: locations(1, 50), 2: locations(2, 50), 3: locations(3, 400)}

    plan, notes = progression_cap.plan_exclusions(capable, {3: 350}, 10_000, 2.0, "seed")

    assert len(plan[3]) == 50
    assert notes and "local progression 350" in notes[0]


def test_no_more_locations_are_excluded_than_there_is_filler() -> None:
    capable = {1: locations(1, 50), 2: locations(2, 50), 3: locations(3, 400)}

    plan, notes = progression_cap.plan_exclusions(capable, {}, 30, 2.0, "seed")

    assert len(plan[3]) == 30
    assert notes and "filler left 30" in notes[0]


def multiworld(filler: int) -> SimpleNamespace:
    all_locations = locations(1, 50) + locations(2, 50) + locations(3, 400)
    items = [SimpleNamespace(player=1, name="key", advancement=True, useful=False)] * 10
    items += [SimpleNamespace(player=2, name="junk", advancement=False, useful=False)] * filler
    worlds = {p: SimpleNamespace(options=SimpleNamespace(local_items=SimpleNamespace(value=set()))) for p in (1, 2, 3, 4)}
    return SimpleNamespace(
        player_ids=(1, 2, 3, 4),  # 4: an observer with no location
        worlds=worlds,
        player_name={1: "A", 2: "B", 3: "Giant", 4: "Bridge"},
        seed=1234,
        itempool=items,
        get_unfilled_locations=lambda: all_locations,
        locations=all_locations,
    )


def test_applied_on_a_multiworld_the_giant_loses_its_surplus_only() -> None:
    mw = multiworld(filler=10_000)

    lines = progression_cap.apply_progression_cap(mw, 2.0, DEFAULT, EXCLUDED)

    excluded = [loc for loc in mw.locations if loc.progress_type == EXCLUDED]
    assert {loc.player for loc in excluded} == {3}
    # The observer does not count: 3 worlds, the giant keeps 2 x (100 + kept) / 3, i.e. 200.
    assert 400 - len(excluded) == 200
    assert any("Giant" in line for line in lines)


def test_already_excluded_locations_use_the_filler_first() -> None:
    mw = multiworld(filler=120)
    # 20 of the giant's locations are already excluded by its own options: 100 filler items remain for the cap.
    for loc in mw.locations[100:120]:
        loc.progress_type = EXCLUDED

    progression_cap.apply_progression_cap(mw, 2.0, DEFAULT, EXCLUDED)

    assert sum(1 for loc in mw.locations if loc.progress_type == EXCLUDED) == 120


def test_the_image_and_the_generator_carry_the_cap() -> None:
    with open(os.path.join(_REPO_ROOT, "Dockerfile"), encoding="utf-8") as handle:
        dockerfile = handle.read()
    assert "COPY progression_cap.py /usr/local/lib/python3.13/site-packages/progression_cap.py" in dockerfile
    guard = next(line for line in dockerfile.splitlines() if line.startswith("RUN python -c \"import "))
    assert "progression_cap" in guard  # the import guard of the build
    with open(os.path.join(_REPO_ROOT, "generate_multiworld.py"), encoding="utf-8") as handle:
        source = handle.read()
    assert source.index("progression_cap.install(") < source.index("ERmain(erargs, seed)")
