"""The exact tracking of a game that needed a ROM to generate, without the ROM (story 44.4).

ArchiLAN never keeps the file a launcher provides. The tracking regenerates the multiworld weeks later, so it
must not run the stages where worlds read their ROM: `stage_assert_generate` (ALttP, OoT, Yoshi's Island check it
exists) and `generate_output` (every world reads it for its patch). Checked on reachable.py's source: importing
it loads Archipelago.
"""
from __future__ import annotations

import ast
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SOURCE = open(os.path.join(_REPO_ROOT, "reachable.py"), encoding="utf-8").read()
_TREE = ast.parse(_SOURCE)


def _string_constants() -> set[str]:
    return {n.value for n in ast.walk(_TREE) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def test_the_tracking_never_runs_the_stages_that_read_a_rom():
    stages = _string_constants()
    assert "assert_generate" not in stages, "reachable.py runs stage_assert_generate: a ROM world would need its file"
    assert "generate_output" not in stages, "reachable.py runs generate_output: every ROM world reads its file there"


def test_the_tracking_never_runs_main_whole():
    imported = {
        alias.name
        for node in ast.walk(_TREE)
        if isinstance(node, ast.ImportFrom) and node.module == "Main"
        for alias in node.names
    }
    assert "main" not in imported, "Main.main runs every stage, the ROM ones included"


def test_a_world_reading_its_file_is_named_instead_of_a_bare_failure():
    calls = {
        n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
        for n in ast.walk(_TREE)
        if isinstance(n, ast.Call)
    }
    assert "required_file_record" in calls
    assert "RequiredFileUnavailable" in calls
    assert "_error_payload" in calls
