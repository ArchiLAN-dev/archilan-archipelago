"""The `_winapi` stub must look absent, not look like a module with a broken `__file__`.

`inspect.getmodule` walks `sys.modules`, keeps every entry for which `hasattr(m, "__file__")` holds,
and then calls `inspect.getabsfile(m)` on it **without a guard**. A stub whose `__getattr__` answers
`0` to everything passes that check and blows up one line later:

    TypeError: <module '_winapi' from 0> is a built-in module

Anything that inspects the call stack then dies. gtfo does, through `importlib.resources.files()`,
which infers its caller from the stack - so the world failed to load for a reason that had nothing to
do with the world.

Raising AttributeError for dunders makes the stub look like the built-in module it stands in for, and
`getmodule` skips it. These tests execute each script's real stub rather than reading it.
"""
from __future__ import annotations

import ast
import inspect
import os
import sys
import types

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPTS = [
    "generate_template.py",
    "generate_multiworld.py",
    "introspect_options.py",
    "reachable.py",
]


def _winapi_getattr(script: str):
    """The stub's resolver, lifted out of a module that cannot be imported here."""
    source = open(os.path.join(_REPO_ROOT, script), encoding="utf-8").read()
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == "_winapi_getattr":
            namespace: dict = {}
            exec(compile(ast.Module(body=[node], type_ignores=[]), "<stub>", "exec"), namespace)

            return namespace["_winapi_getattr"]

    raise AssertionError(f"_winapi_getattr not found in {script} - every script must carry it")


@pytest.mark.parametrize("script", SCRIPTS)
def test_dunders_are_absent(script: str) -> None:
    resolver = _winapi_getattr(script)

    for dunder in ("__file__", "__path__", "__loader__", "__spec__"):
        with pytest.raises(AttributeError):
            resolver(dunder)


@pytest.mark.parametrize("script", SCRIPTS)
def test_everything_else_still_answers_zero(script: str) -> None:
    # The point of the stub in the first place: Windows-only calls resolve to something harmless
    # instead of failing the import.
    resolver = _winapi_getattr(script)

    assert resolver("CreateProcess") == 0
    assert resolver("WaitForSingleObject") == 0


@pytest.mark.parametrize("script", SCRIPTS)
def test_stack_inspection_survives_the_stub(script: str) -> None:
    """The failure gtfo hit, reproduced end to end."""
    stub = types.ModuleType("_winapi")
    stub.__getattr__ = _winapi_getattr(script)  # type: ignore[method-assign]

    previous = sys.modules.get("_winapi")
    sys.modules["_winapi"] = stub
    try:
        assert inspect.stack(), "inspect.stack() must not raise with the stub installed"
    finally:
        if previous is None:
            del sys.modules["_winapi"]
        else:
            sys.modules["_winapi"] = previous
