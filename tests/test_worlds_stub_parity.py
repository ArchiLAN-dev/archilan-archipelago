"""Every script that stands in for the `worlds` package must do it the same way.

`generate_template.py`, `generate_multiworld.py` and `introspect_options.py` each install a fake
`worlds` module before loading any apworld. Whatever one of them arms and the others do not becomes
a world that loads on one path and not the others - and the difference only surfaces against a real
apworld, in production, long after the fact.

Two mechanisms carry that risk, and both lived in `generate_template.py` alone for months:

- `_worlds_getattr` resolves `worlds.<name>` as a submodule on first attribute access. A world that
  writes `worlds.Files.APDeltaPatch` without importing it (jurassic_park) needs it. A full
  generation happens to survive without it, because a neighbouring world imported the submodule
  first; a script that loads one world in isolation has no neighbour.
- `_permissive_choice_meta_new` strips `option_random*` when Archipelago's metaclass asserts on it.
  Three worlds - rune4, smash64, untitled_goose_game - define it anyway.

Both are strictly failure-reducing: they only run where the unpatched code raises. There is no
reason for one script to have them and another not, which is exactly why this test exists.
"""
from __future__ import annotations

import ast
import os

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# generate_template.py first: it is the reference, having carried both mechanisms all along.
SCRIPTS = ["generate_template.py", "generate_multiworld.py", "introspect_options.py"]

REQUIRED_FUNCTIONS = ["_worlds_getattr", "_permissive_choice_meta_new"]


def _parse(script: str) -> ast.Module:
    return ast.parse(open(os.path.join(_REPO_ROOT, script), encoding="utf-8").read())


def _function_names(script: str) -> set[str]:
    return {n.name for n in ast.walk(_parse(script)) if isinstance(n, ast.FunctionDef)}


def _stub_attributes(script: str) -> set[str]:
    """Every `_worlds_stub.<name> = ...` the script performs."""
    attributes: set[str] = set()
    for node in ast.walk(_parse(script)):
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
@pytest.mark.parametrize("function", REQUIRED_FUNCTIONS)
def test_every_script_arms_both_mechanisms(script: str, function: str) -> None:
    assert function in _function_names(script), (
        f"{script} is missing {function}; a world that needs it will load on the other paths "
        "and fail on this one, which is the hardest kind of difference to find."
    )


@pytest.mark.parametrize("script", SCRIPTS)
def test_the_lazy_resolver_is_actually_installed(script: str) -> None:
    # Defining the function without wiring it to the stub would pass the test above and do nothing.
    assert "__getattr__" in _stub_attributes(script), f"{script} defines the resolver but never arms it"


def test_no_script_furnishes_the_stub_differently() -> None:
    attributes = {s: _stub_attributes(s) for s in SCRIPTS}
    shared = set.intersection(*attributes.values())
    everything = set.union(*attributes.values())

    # Legitimately one-sided, and listed rather than tolerated in silence: `__file__` is set only
    # where a world resolves paths against it, and `network_data_package` only matters when a seed
    # is actually produced.
    allowed = {"__file__", "network_data_package"}

    assert (everything - shared) <= allowed, (
        "these scripts furnish the `worlds` stub differently: "
        f"{sorted((everything - shared) - allowed)}"
    )
