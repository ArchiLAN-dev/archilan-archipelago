"""An apworld named like a built-in world replaces it in every script (story 38.29).

The updated Ocarina of Time apworld (9.1) ships its package as `oot`, the name of the release's own
OoT folder world (7.0). The template and introspection scripts put an apworld ahead of that folder on
`worlds.__path__`; the generation and reachability scripts put it behind. So the template asked for
9.1.0 and the generation loaded 7.0.0, refusing every YAML: "required version of world "Ocarina of
Time" is at least 9.1.0, however world is of version 7.0.0".
"""
from __future__ import annotations

import ast
import os
import sys
import types

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPTS = ["generate_template.py", "generate_multiworld.py", "introspect_options.py", "reachable.py"]


def _path_calls(script: str) -> list[str]:
    """The method of every `<x>.__path__.<method>(...)` call in the script."""
    tree = ast.parse(open(os.path.join(_REPO_ROOT, script), encoding="utf-8").read())
    calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "__path__"
        ):
            calls.append(node.func.attr)
    return calls


@pytest.mark.parametrize("script", SCRIPTS)
def test_every_script_puts_an_apworld_ahead_of_the_built_in_worlds(script: str) -> None:
    calls = _path_calls(script)

    assert "insert" in calls
    assert "append" not in calls


def test_a_package_inserted_first_shadows_the_built_in_one(tmp_path, monkeypatch) -> None:
    built_in = tmp_path / "release" / "toyworld"
    apworld = tmp_path / "apworld" / "toyworld"
    for folder, version in ((built_in, "7.0.0"), (apworld, "9.1.0")):
        folder.mkdir(parents=True)
        (folder / "__init__.py").write_text(f"VERSION = {version!r}\n", encoding="utf-8")
    stub = types.ModuleType("toyworlds")
    stub.__path__ = [str(tmp_path / "release")]
    monkeypatch.setitem(sys.modules, "toyworlds", stub)

    stub.__path__.insert(0, str(tmp_path / "apworld"))
    module = __import__("toyworlds.toyworld", fromlist=["VERSION"])

    assert module.VERSION == "9.1.0"
    monkeypatch.delitem(sys.modules, "toyworlds.toyworld")
