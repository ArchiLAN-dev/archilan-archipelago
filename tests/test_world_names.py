"""What `world_names` reads out of a World class for the option editors (stories 4.14 and 9.58)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world_names import item_groups, static_items, static_locations  # noqa: E402


def _world(**tables):
    return type("World", (), tables)


def test_items_and_locations_are_the_sorted_keys_of_their_tables():
    world = _world(
        item_name_to_id={"Mantis_Claw": 2, "Dream_Nail": 1},
        location_name_to_id={"Grubfather_1": 10, "Crossroads_Stag": 11},
    )

    assert static_items(world) == ["Dream_Nail", "Mantis_Claw"]
    assert static_locations(world) == ["Crossroads_Stag", "Grubfather_1"]


def test_a_world_without_tables_has_empty_lists():
    world = _world()

    assert static_items(world) == []
    assert static_locations(world) == []
    assert item_groups(world) == {}


def test_groups_are_sorted_and_everything_is_left_out():
    world = _world(item_name_groups={
        "Skills": {"Mantis_Claw", "Dream_Nail"},
        "Everything": {"Mantis_Claw", "Dream_Nail", "Grub"},
        "Dreamers": ["Lurien", "Herrah"],
    })

    assert item_groups(world) == {
        "Dreamers": ["Herrah", "Lurien"],
        "Skills": ["Dream_Nail", "Mantis_Claw"],
    }


def test_a_group_that_is_not_a_list_of_names_is_dropped():
    world = _world(item_name_groups={"Broken": 3, "Text": "Grub", "Empty": set(), "Ok": {"Grub"}})

    assert item_groups(world) == {"Ok": ["Grub"]}
