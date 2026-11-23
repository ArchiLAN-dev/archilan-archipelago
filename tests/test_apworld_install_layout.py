"""An extracted apworld sits at `<temp>/worlds/<pkg>`, the layout of an Archipelago install.

Super Metroid's VARIA randomizer takes `Path(__file__).parents[4]` as the install root and opens
`worlds/sm/variaRandomizer/standard_presets/regular.json` under it. Extracted at `<temp>/sm`, the root was
`/tmp` and the default YAML failed on a missing preset (apworld health, image regression of 2026-11-08).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from generation_setup import apworld_extract_dir  # noqa: E402


def test_a_world_finds_its_data_from_the_install_root_it_computes() -> None:
    worlds = Path(apworld_extract_dir())
    presets = worlds / "sm" / "variaRandomizer" / "standard_presets"
    presets.mkdir(parents=True)
    (presets / "regular.json").write_text("{}", encoding="utf-8")
    parameters = worlds / "sm" / "variaRandomizer" / "utils" / "parameters.py"
    parameters.parent.mkdir(parents=True)
    parameters.write_text("", encoding="utf-8")

    app_dir = parameters.parents[4]

    assert worlds.name == "worlds"
    assert (app_dir / "worlds/sm/variaRandomizer/standard_presets/regular.json").is_file()


def test_each_apworld_gets_its_own_directory() -> None:
    assert apworld_extract_dir() != apworld_extract_dir()
