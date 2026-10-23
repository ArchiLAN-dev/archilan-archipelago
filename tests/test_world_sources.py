"""`worlds.world_sources` exists for the apworlds that read it (story 38.21).

Archipelago's worlds/__init__.py indexes every world it finds in `world_sources`. pokemon_bw reads it
at import time to detect its optional companion:

    from worlds import world_sources
    DEXSANITYSANITY_ENABLED = any(Path(source.path).stem == "pokemon_bw_dexsanitysanity" for source in world_sources)

Our scripts replace `worlds` with a stub that never runs that file. Without the attribute the import
raised `ImportError: cannot import name 'world_sources' from 'worlds'` and generate_template.py
reported "No game registered from the apworld(s)", the misleading verdict production showed.
"""
from __future__ import annotations

import ast
import os
import sys
import types
from pathlib import Path

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

import generation_setup  # noqa: E402

SCRIPTS = ["generate_template.py", "generate_multiworld.py", "introspect_options.py"]


def _touch(directory: Path, *names: str) -> None:
    for name in names:
        (directory / name).write_bytes(b"")


def test_every_apworld_of_the_directory_is_a_source(tmp_path: Path) -> None:
    _touch(tmp_path, "pokemon_bw_dexsanitysanity.apworld", "pokemon_bw.apworld", "notes.txt")

    sources = generation_setup.apworld_sources(str(tmp_path))

    assert [s.name for s in sources] == ["pokemon_bw", "pokemon_bw_dexsanitysanity"]
    assert all(s.is_zip and not s.relative for s in sources)
    assert sources[0].resolved_path == str(tmp_path / "pokemon_bw.apworld")


def test_a_missing_directory_has_no_source(tmp_path: Path) -> None:
    assert generation_setup.apworld_sources(str(tmp_path / "absent")) == []
    assert generation_setup.apworld_sources("") == []


def _pokemon_bw_detection(sources: list) -> bool:
    """pokemon_bw/options/sanity.py, run against a `worlds` module holding `sources`."""
    stub = types.ModuleType("worlds")
    stub.world_sources = sources
    saved = sys.modules.get("worlds")
    sys.modules["worlds"] = stub
    try:
        namespace: dict = {}
        exec(
            "from pathlib import Path\n"
            "from worlds import world_sources\n"
            "DEXSANITYSANITY_ENABLED = any(Path(source.path).stem == 'pokemon_bw_dexsanitysanity' for source in world_sources)\n",
            namespace,
        )
        return namespace["DEXSANITYSANITY_ENABLED"]
    finally:
        if saved is None:
            del sys.modules["worlds"]
        else:
            sys.modules["worlds"] = saved


def test_pokemon_bw_imports_and_sees_its_companion(tmp_path: Path) -> None:
    _touch(tmp_path, "pokemon_bw.apworld", "pokemon_bw_dexsanitysanity.apworld")

    assert _pokemon_bw_detection(generation_setup.apworld_sources(str(tmp_path))) is True


def test_pokemon_bw_imports_without_its_companion(tmp_path: Path) -> None:
    _touch(tmp_path, "pokemon_bw.apworld")

    assert _pokemon_bw_detection(generation_setup.apworld_sources(str(tmp_path))) is False


def _calls(script: str, function: str) -> list[ast.Call]:
    tree = ast.parse(Path(_REPO_ROOT, script).read_text(encoding="utf-8"))
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == function
    ]


@pytest.mark.parametrize("script", SCRIPTS)
def test_every_script_lists_its_apworlds_before_loading_them(script: str) -> None:
    assert _calls(script, "apworld_sources"), f"{script} never fills worlds.world_sources: pokemon_bw cannot import there"

    # Listed before the loop that imports them: a companion sorting after its world must already be there.
    source = Path(_REPO_ROOT, script).read_text(encoding="utf-8")
    assert source.index("apworld_sources(") < source.index('glob("*.apworld")')
