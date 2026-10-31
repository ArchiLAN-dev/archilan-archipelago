"""A failed output no longer hangs the generation (story 38.24).

Ocarina of Time opens its ROM in `generate_output` before setting `collectible_flags_available`, and
`fill_slot_data` waits on that event with no timeout. "Ocarina of Time but it's just Master Quest Water Temple"
skips OoT's upfront ROM check, so without the ROM the generation hung until the orchestrator's 5 minute deadline.
"""
from __future__ import annotations

import os
import sys
import threading
import types

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

import generation_setup  # noqa: E402


class FakeWorld:
    def __init__(self, outcome):
        self.outcome = outcome
        self.collectible_flags_available = threading.Event()
        self.hint_data_available = threading.Event()

    def generate_output(self, output_directory):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def fill_slot_data(self):
        raise RuntimeError("slot data")


def _module():
    def call_single(multiworld, method_name, player, *args):
        return getattr(multiworld.worlds[player], method_name)(*args)

    return types.SimpleNamespace(call_single=call_single)


def _multiworld(world):
    return types.SimpleNamespace(worlds={1: world})


def test_a_failed_output_releases_the_world_events_and_raises_the_same_error():
    module = _module()
    generation_setup.release_events_on_output_failure(module)
    error = FileNotFoundError("The Legend of Zelda - Ocarina of Time.z64 does not exist, but OOTSettings.rom_file is required")
    error.__notes__ = ["Exception in <bound method> for player 1, named Test."]  # add_note() needs Python 3.11
    world = FakeWorld(error)

    with pytest.raises(FileNotFoundError) as raised:
        module.call_single(_multiworld(world), "generate_output", 1, "/tmp/output")

    assert raised.value is error
    assert raised.value.__notes__ == ["Exception in <bound method> for player 1, named Test."]
    assert world.collectible_flags_available.is_set()
    assert world.hint_data_available.is_set()


def test_a_waiting_stage_moves_on_once_the_output_failed():
    module = _module()
    generation_setup.release_events_on_output_failure(module)
    world = FakeWorld(FileNotFoundError("missing"))
    waiter = threading.Thread(target=world.collectible_flags_available.wait)
    waiter.start()

    with pytest.raises(FileNotFoundError):
        module.call_single(_multiworld(world), "generate_output", 1, "/tmp/output")

    waiter.join(timeout=5)
    assert not waiter.is_alive()


def test_a_successful_output_or_another_stage_leaves_the_events_alone():
    module = _module()
    generation_setup.release_events_on_output_failure(module)
    world = FakeWorld("done")

    assert module.call_single(_multiworld(world), "generate_output", 1, "/tmp/output") == "done"
    with pytest.raises(RuntimeError):
        module.call_single(_multiworld(world), "fill_slot_data", 1)

    assert not world.collectible_flags_available.is_set()
    assert not world.hint_data_available.is_set()


def test_installing_twice_wraps_once():
    module = _module()
    generation_setup.release_events_on_output_failure(module)
    wrapped = module.call_single
    generation_setup.release_events_on_output_failure(module)

    assert module.call_single is wrapped
