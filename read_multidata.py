#!/usr/bin/env python3
"""Read the slot table of an Archipelago multidata and emit JSON to stdout.

Story 16.18: a member can create a party from a seed generated somewhere else. The archive is
the only thing we get, so its slots have to be read out of it rather than derived from the game
selection nobody made here.

Why this runs in a container: a multidata is a zlib-compressed pickle, and the file comes from
outside. `pickle` executes code while deserialising, so this uses Archipelago's own
``Utils.restricted_loads`` - the allowlisting unpickler ``MultiServer`` uses to load the very
same file - and nothing else. The container is one-shot and network-disabled on top.

Output shape::

    {"seedName": "...", "slots": [{"slot": 1, "name": "Alice", "game": "Minecraft", "type": 1}]}

``type`` is Archipelago's ``SlotType``: 0 spectator, 1 player, 2 group (an item-link pseudo
slot). The caller decides what to do with the non-players; this script reports them rather than
hiding them, so a surprising archive is visible instead of silently trimmed.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import zipfile
import zlib

_ap_src = "/app/ArchipelagoSrc"
if os.path.isdir(_ap_src) and _ap_src not in sys.path:
    sys.path.insert(0, _ap_src)


def _fail(message: str) -> None:
    print(json.dumps({"error": message}))
    sys.exit(1)


def _multidata_bytes(path: str) -> bytes:
    """The raw multidata, out of a whole output archive or straight from a bare file."""
    with open(path, "rb") as handle:
        data = handle.read()

    if not zipfile.is_zipfile(io.BytesIO(data)):
        return data

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = [n for n in archive.namelist() if n.endswith(".archipelago")]
        if not names:
            _fail("no .archipelago file in the archive")
        if len(names) > 1:
            _fail("several .archipelago files in the archive")
        # Bounded read: a zip bomb must not be able to fill the container's disk before the
        # unpickler ever gets a say.
        info = archive.getinfo(names[0])
        if info.file_size > 256 * 1024 * 1024:
            _fail("multidata is too large")
        return archive.read(names[0])


def _load(path: str) -> dict:
    from Utils import restricted_loads  # noqa: PLC0415 - only importable inside the AP image

    data = _multidata_bytes(path)
    try:
        # The first byte is the multidata format version, exactly as MultiServer reads it.
        decoded = restricted_loads(zlib.decompress(data[1:]))
    except Exception as exc:  # noqa: BLE001 - any failure here means "not a usable multidata"
        _fail(f"unreadable multidata: {exc.__class__.__name__}: {exc}")

    if not isinstance(decoded, dict):
        _fail("multidata is not a mapping")

    return decoded


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True, help="output .zip or bare .archipelago file")
    args = parser.parse_args()

    if not os.path.isfile(args.archive):
        _fail(f"no such file: {args.archive}")

    multidata = _load(args.archive)

    slot_info = multidata.get("slot_info")
    if not isinstance(slot_info, dict) or not slot_info:
        _fail("multidata carries no slot_info")

    slots = []
    for number, info in sorted(slot_info.items(), key=lambda item: int(item[0])):
        slots.append(
            {
                "slot": int(number),
                "name": str(getattr(info, "name", "")),
                "game": str(getattr(info, "game", "")),
                "type": int(getattr(info, "type", 1)),
            }
        )

    seed_name = multidata.get("seed_name", "")
    print(json.dumps({"seedName": str(seed_name), "slots": slots}))


if __name__ == "__main__":
    main()
