"""Both scripts must prepare the fake `worlds` package the same way.

`introspect_options.py` and `generate_multiworld.py` each install a stand-in for the `worlds`
package before loading any apworld. Whatever one attaches to it and the other does not becomes a
world that loads on one path and not the other - and the difference only shows up against a real
apworld, in production, hours later.

`worlds.Files` is the case that bit: `jurassic_park` writes `worlds.Files.APDeltaPatch` without
importing the submodule. A full generation gets it for free, because some neighbouring world in the
catalogue imported it first; an introspection loads a single world in isolation and never does.

This is the second divergence of its kind - `orjson` was the first, see test_orjson_shim.py. The
third should fail here rather than in a log nobody reads.
"""
from __future__ import annotations

import ast
import os

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPTS = ["introspect_options.py", "generate_multiworld.py"]


def _stub_attributes(script: str) -> set[str]:
    """Every `_worlds_stub.<name> = ...` the script performs at module level."""
    source = open(os.path.join(_REPO_ROOT, script), encoding="utf-8").read()

    attributes: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "_worlds_stub"
            ):
                attributes.add(target.attr)

    return attributes


@pytest.mark.parametrize("script", SCRIPTS)
def test_the_stub_carries_files(script: str) -> None:
    """The submodule jurassic_park reads without importing it."""
    assert "Files" in _stub_attributes(script), (
        f"{script} does not attach worlds.Files to its stub; a world that reads it without "
        "importing it will fail to load on this path only."
    )


def test_neither_script_knows_something_the_other_does_not() -> None:
    introspect, generate = (_stub_attributes(s) for s in SCRIPTS)

    # Two attributes are legitimately one-sided and stay out of the comparison: `__file__` is set
    # only where a world resolves paths against it, and `network_data_package` only matters when a
    # seed is actually produced.
    allowed = {"__file__", "network_data_package"}

    assert (introspect ^ generate) <= allowed, (
        "the two scripts prepare the `worlds` stub differently: "
        f"{sorted((introspect ^ generate) - allowed)}"
    )
