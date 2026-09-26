"""Regression tests for replaying a world's own rolls from its slot_data in the reachability pass.

reachable.py regenerates the world with a fresh seed, so everything the world rolled at generation
comes out different. Hollow Knight rolls a lot that logic depends on: how many slots each shop has
(`EggShopSlots: random-range-0-16`...), the grub/essence/charm price of every shop location, the
charm notch costs. Its fill_slot_data writes all of it back (`options`, `location_costs`,
`notch_costs`, `grub_count`), but HK has no `interpret_slot_data`, so nothing replayed it: the pass
answered on a different set of shop locations with different prices.

`_replay_slot_data` puts the seed's values back at the stage where the world reads them. The
functions are compiled straight from reachable.py: that module imports Archipelago at module level,
which only exists inside the AP container.
"""
from __future__ import annotations

import ast
import os
import types

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WANTED = {"_hk_replay", "_replay_slot_data", "_SLOT_DATA_REPLAY"}


def _load():
    with open(os.path.join(_REPO_ROOT, "reachable.py"), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    body = [
        node
        for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in _WANTED)
        or (isinstance(node, ast.Assign) and any(getattr(t, "id", None) in _WANTED for t in node.targets))
    ]
    namespace: dict = {}
    exec(compile(ast.Module(body=body, type_ignores=[]), "reachable.py", "exec"), namespace)
    return namespace["_replay_slot_data"]


class FakeOption:
    def __init__(self, value: int) -> None:
        self.value = value


class FakeLocation:
    def __init__(self, name: str, costs: dict | None) -> None:
        self.name = name
        self.costs = costs


class FakeHK:
    game = "Hollow Knight"

    def __init__(self) -> None:
        self.player = 1
        self.options = types.SimpleNamespace(EggShopSlots=FakeOption(3), GrubHuntGoal=FakeOption(46))
        self.charm_costs = [1] * 40
        self.grub_count = 46
        self.grub_player_count = {1: 46}
        self.locations = [FakeLocation("Grubfather_1", {"GRUBS": 4}), FakeLocation("Sly_1", {"GEO": 100})]
        self.multiworld = types.SimpleNamespace(get_locations=lambda player: self.locations)


SLOT_DATA = {
    "options": {"EggShopSlots": 11, "GrubHuntGoal": 20, "UnknownOption": 1},
    "notch_costs": [2] * 40,
    "grub_count": 20,
    "location_costs": {"Grubfather_1": {"GRUBS": 17}, "Egg_Shop_1": {"RANCIDEGGS": 5}},
}


def test_the_seeds_options_replace_the_rolled_ones_before_generate_early():
    """Shop slot counts are options rolled from a random range: they decide which locations exist."""
    replay = _load()
    world = FakeHK()

    replay("before_generate_early", world, SLOT_DATA)

    assert world.options.EggShopSlots.value == 11
    assert world.options.GrubHuntGoal.value == 20


def test_notch_and_grub_counts_come_back_after_generate_early():
    """generate_early rolls both; logic reads them afterwards."""
    replay = _load()
    world = FakeHK()

    replay("after_generate_early", world, SLOT_DATA)

    assert world.charm_costs == [2] * 40
    assert world.grub_count == 20
    assert world.grub_player_count == {1: 20}


def test_location_costs_come_back_before_set_rules():
    """set_rules captures each cost in a closure, so the seed's prices must be in place before it runs."""
    replay = _load()
    world = FakeHK()

    replay("before_set_rules", world, SLOT_DATA)

    assert world.locations[0].costs == {"GRUBS": 17}
    assert world.locations[1].costs == {"GEO": 100}, "a location the seed did not price keeps its own"


def test_a_world_without_a_replay_is_left_alone():
    replay = _load()
    world = FakeHK()
    world.game = "Sayonara Wild Hearts"

    replay("before_generate_early", world, SLOT_DATA)

    assert world.options.EggShopSlots.value == 3


def test_an_empty_slot_data_changes_nothing():
    replay = _load()
    world = FakeHK()

    for stage in ("before_generate_early", "after_generate_early", "before_set_rules"):
        replay(stage, world, {})

    assert world.options.EggShopSlots.value == 3
    assert world.charm_costs == [1] * 40
    assert world.locations[0].costs == {"GRUBS": 4}


class FakeShopHK(FakeHK):
    """Shops as HK builds them: a list of locations per shop, numbered from 1, all in Menu."""

    def __init__(self, counts: dict[str, int]) -> None:
        super().__init__()
        self.region = types.SimpleNamespace(locations=[])
        self.created_multi_locations = {shop: [] for shop in counts}
        for shop, count in counts.items():
            for _ in range(count):
                self.create_location(shop)
        self.multiworld = types.SimpleNamespace(
            get_region=lambda name, player: self.region,
            get_locations=lambda player: list(self.region.locations),
        )

    def create_location(self, name: str) -> FakeLocation:
        shop = self.created_multi_locations[name]
        location = FakeLocation(f"{name}_{len(shop) + 1}", None)
        shop.append(location)
        self.region.locations.append(location)
        return location


def test_every_shop_gets_the_seeds_number_of_slots_after_create_items():
    """ExtraShopSlots are spread over the shops at random in create_items: the regeneration handed
    Egg_Shop two slots too many and Grubfather one too few. Every shop location has a price, so the
    seed's `location_costs` says how many each shop really has."""
    replay = _load()
    world = FakeShopHK({"Egg_Shop": 14, "Grubfather": 15, "Sly_(Key)": 2})
    slot_data = {"location_costs": {
        **{f"Egg_Shop_{i}": {"RANCIDEGGS": i} for i in range(1, 13)},
        **{f"Grubfather_{i}": {"GRUBS": i} for i in range(1, 17)},
        **{f"Sly_(Key)_{i}": {"GEO": i} for i in range(1, 3)},
    }}

    replay("after_create_items", world, slot_data)

    names = {location.name for location in world.region.locations}
    assert names == set(slot_data["location_costs"])
    assert len(world.created_multi_locations["Egg_Shop"]) == 12
    assert len(world.created_multi_locations["Grubfather"]) == 16
