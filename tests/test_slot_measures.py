"""Size and progression share of each slot (story 56.1).

A reference multidata of three players, one of them much bigger, plus the Bridge observer and an item-link
group: the big world must come out holding most of the others' progression, the observer and the group must
not appear, and filler or useful items must not count.
"""
from __future__ import annotations

import io
import os
import pickle
import sys
import zipfile
import zlib
from collections import namedtuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

import generation_setup  # noqa: E402
import slot_measures  # noqa: E402

Slot = namedtuple("Slot", ["name", "game", "type", "group_members"])

PROG, USEFUL, FILLER = 0b001, 0b010, 0b000


def reference_multidata() -> dict:
    # Slot 1 observes, 2 and 3 are small worlds, 4 is the big one, 5 is an item-link group.
    slot_info = {
        1: Slot("Bridge", "Archipelago", 0, ()),
        2: Slot("Alice", "Ocarina of Time", 1, ()),
        3: Slot("Bob", "Hollow Knight", 1, ()),
        4: Slot("Carol", "Stardew Valley", 1, ()),
        5: Slot("Link", "Hollow Knight", 2, (3,)),
    }
    locations = {
        # Alice: 4 locations - her own key, Bob's key, a useful item of Carol, a filler.
        2: {10: (1, 2, PROG), 11: (2, 3, PROG), 12: (3, 4, USEFUL), 13: (4, 2, FILLER)},
        # Bob: 3 locations - two of his own keys, one filler of Alice.
        3: {20: (5, 3, PROG), 21: (6, 3, PROG | USEFUL), 22: (7, 2, FILLER)},
        # Carol: 10 locations - 5 keys of Alice, 3 of Bob, 1 of hers, 1 filler.
        4: {
            **{30 + i: (8, 2, PROG) for i in range(5)},
            **{40 + i: (9, 3, PROG) for i in range(3)},
            50: (10, 4, PROG),
            51: (11, 4, FILLER),
        },
    }
    return {"slot_info": slot_info, "locations": locations, "seed_name": "56"}


def by_name(records: list[dict]) -> dict[str, dict]:
    return {record["name"]: record for record in records}


def test_each_player_gets_its_size_and_both_progression_counts() -> None:
    measures = by_name(slot_measures.measure(reference_multidata()))

    assert set(measures) == {"Alice", "Bob", "Carol"}
    assert measures["Alice"] == {"slot": 2, "name": "Alice", "game": "Ocarina of Time", "locations": 4,
                                 "ownProgression": 6, "hostedProgression": 1}
    assert measures["Bob"] == {"slot": 3, "name": "Bob", "game": "Hollow Knight", "locations": 3,
                               "ownProgression": 6, "hostedProgression": 0}
    assert measures["Carol"] == {"slot": 4, "name": "Carol", "game": "Stardew Valley", "locations": 10,
                                 "ownProgression": 1, "hostedProgression": 8}


def test_the_big_world_holds_most_of_the_others_progression() -> None:
    measures = slot_measures.measure(reference_multidata())
    total = sum(record["hostedProgression"] for record in measures)
    carol = by_name(measures)["Carol"]

    assert carol["hostedProgression"] / total > 0.8


def test_a_lone_slot_hosts_nothing_of_the_others() -> None:
    multidata = {
        "slot_info": {1: Slot("Solo", "Hollow Knight", 1, ())},
        "locations": {1: {1: (1, 1, PROG), 2: (2, 1, FILLER)}},
    }

    assert slot_measures.measure(multidata) == [
        {"slot": 1, "name": "Solo", "game": "Hollow Knight", "locations": 2, "ownProgression": 1,
         "hostedProgression": 0},
    ]


def test_a_world_without_locations_counts_zero() -> None:
    multidata = {"slot_info": {1: Slot("Empty", "Archipelago", 1, ())}, "locations": {}}

    assert slot_measures.measure(multidata)[0]["locations"] == 0


def _archive(tmp_path, multidata: dict) -> str:
    raw = b"\x03" + zlib.compress(pickle.dumps(multidata))
    path = tmp_path / "AP_56.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("AP_56.archipelago", raw)
        archive.writestr("AP_56_Spoiler.txt", "spoiler")
    return str(path)


def test_measures_line_reads_the_output_just_written(tmp_path) -> None:
    import json

    line = generation_setup.measures_line(_archive(tmp_path, reference_multidata()), loads=pickle.loads)

    assert line is not None and line.startswith(generation_setup.MEASURES_SENTINEL + " ")
    records = json.loads(line[len(generation_setup.MEASURES_SENTINEL) + 1:])
    assert by_name(records)["Carol"]["hostedProgression"] == 8


def test_measures_line_never_raises_on_an_unreadable_output(tmp_path, capsys) -> None:
    path = tmp_path / "broken.archipelago"
    path.write_bytes(b"\x03not zlib")

    assert generation_setup.measures_line(str(path), loads=pickle.loads) is None
    assert "slot measures unavailable" in capsys.readouterr().err


def test_read_multidata_and_the_image_carry_the_module() -> None:
    with open(os.path.join(_REPO_ROOT, "read_multidata.py"), encoding="utf-8") as handle:
        assert "from slot_measures import measure" in handle.read()
    with open(os.path.join(_REPO_ROOT, "Dockerfile"), encoding="utf-8") as handle:
        dockerfile = handle.read()
    assert "COPY slot_measures.py /usr/local/lib/python3.13/site-packages/slot_measures.py" in dockerfile
    guard = next(line for line in dockerfile.splitlines() if line.startswith("RUN python -c \"import "))
    assert "slot_measures" in guard  # the import guard of the build


def test_generate_multiworld_prints_the_measures_before_the_output_name() -> None:
    with open(os.path.join(_REPO_ROOT, "generate_multiworld.py"), encoding="utf-8") as handle:
        source = handle.read()
    assert source.index("measures_line(") < source.index("print(_out_files[0].name, flush=True)")
