"""The tkinter stubs must not answer `__file__` either - the `_winapi` lesson, applied to tkinter.

`importlib.resources.files()` without an argument infers its caller from the stack; `inspect` then
walks `sys.modules` and reads each module's `__file__`. The tkinter stub answered *every* attribute
with itself, `__file__` included, so `inspect` took the stub for a file name and failed with
`TypeError: 'module' object is not callable`. gtfo does exactly that call at import, and its world
failed to load ("No world found to handle game GTFO") for a reason that had nothing to do with it.

The same holds for the modules `apworld_import` stubs on demand: they must not answer `__file__`, but
must keep answering `__path__`, or a world importing a sub-module of a stubbed package (zillion and
`zilliandomizer.*`) no longer loads. These tests execute the real stubs rather than reading them.
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
import os
import sys
import types

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

SCRIPTS = [
    "generate_template.py",
    "generate_multiworld.py",
    "introspect_options.py",
    "reachable.py",
]

_SENTINEL = types.ModuleType("tkinter")


def _tk_getattr(script: str):
    """The stub's resolver, lifted out of a module that cannot be imported here."""
    source = open(os.path.join(_REPO_ROOT, script), encoding="utf-8").read()
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == "_tk_getattr":
            namespace: dict = {"_tk_stub": _SENTINEL}
            exec(compile(ast.Module(body=[node], type_ignores=[]), "<stub>", "exec"), namespace)

            return namespace["_tk_getattr"]

    raise AssertionError(f"_tk_getattr not found in {script} - every script must carry it")


@pytest.mark.parametrize("script", SCRIPTS)
def test_tkinter_dunders_are_absent(script: str) -> None:
    resolver = _tk_getattr(script)

    for dunder in ("__file__", "__path__", "__loader__", "__spec__"):
        with pytest.raises(AttributeError):
            resolver(dunder)


@pytest.mark.parametrize("script", SCRIPTS)
def test_tkinter_names_still_resolve_to_the_stub(script: str) -> None:
    # The point of the stub: `tkinter.Tk`, `tkinter.messagebox.showerror` import and resolve.
    resolver = _tk_getattr(script)

    assert resolver("Tk") is _SENTINEL
    assert resolver("showerror") is _SENTINEL


@pytest.mark.parametrize("script", SCRIPTS)
def test_stack_inspection_survives_the_tkinter_stub(script: str) -> None:
    """The failure gtfo hit, reproduced end to end."""
    stub = types.ModuleType("tkinter")
    stub.__getattr__ = _tk_getattr(script)  # type: ignore[method-assign]

    previous = sys.modules.get("tkinter")
    sys.modules["tkinter"] = stub
    try:
        assert inspect.stack(), "inspect.stack() must not raise with the stub installed"
    finally:
        if previous is None:
            del sys.modules["tkinter"]
        else:
            sys.modules["tkinter"] = previous


def _on_demand_stub(name: str) -> types.ModuleType:
    from apworld_import import OnDemandStubFinder

    finder = OnDemandStubFinder()
    finder.stubbed.add(name.split(".")[0])
    spec = finder.find_spec(name, None)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    finder.exec_module(module)

    return module


def test_an_on_demand_stub_has_no_file_but_keeps_its_path() -> None:
    from apworld_import import Stub

    module = _on_demand_stub("zilliandomizer_like")

    assert not hasattr(module, "__file__"), "a stub answering __file__ breaks inspect"
    assert isinstance(module.__path__, Stub), "__path__ must answer, or its sub-modules cannot import"
    assert isinstance(module.anything, Stub)


def test_stack_inspection_survives_an_on_demand_stub() -> None:
    module = _on_demand_stub("requests_like")

    sys.modules["requests_like"] = module
    try:
        assert inspect.stack(), "inspect.stack() must not raise with the stub installed"
    finally:
        del sys.modules["requests_like"]
