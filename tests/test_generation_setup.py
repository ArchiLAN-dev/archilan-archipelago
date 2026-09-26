"""The preparation every script shares before loading apworlds and generating (story 9.55).

`generate_multiworld.py` prepared Archipelago with two mechanisms that `reachable.py` never had:

- the permissive `Choice` metaclass, without which rune4, smash64 and untitled_goose_game do not even
  load (they define `option_random`, a name Archipelago reserves);
- the host permission gates (`allow_*` / `enable_*` settings), without which a player option that
  needs one makes Generate raise.

reachable.py runs Generate on every yaml of the session, so a run holding one of those worlds or
options generated fine and then had no reachability at all - on both of its paths. The copies lived
in four scripts; they now live once, in `generation_setup.py`, and every script calls it. These tests
guard both the helpers and the fact that each script really uses them.
"""
from __future__ import annotations

import ast
import os
import sys
import types

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

import generation_setup  # noqa: E402

LOADING_SCRIPTS = ["generate_template.py", "generate_multiworld.py", "introspect_options.py", "reachable.py"]
GENERATING_SCRIPTS = ["generate_multiworld.py", "reachable.py"]


def _calls(script: str) -> set[str]:
    tree = ast.parse(open(os.path.join(_REPO_ROOT, script), encoding="utf-8").read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            names.add(func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", ""))
    return names


def _defines(script: str) -> set[str]:
    tree = ast.parse(open(os.path.join(_REPO_ROOT, script), encoding="utf-8").read())
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


@pytest.mark.parametrize("script", LOADING_SCRIPTS)
def test_every_script_arms_the_permissive_choice_metaclass(script):
    assert "install_permissive_choice_meta" in _calls(script), (
        f"{script} does not arm the permissive Choice metaclass: rune4, smash64 and "
        "untitled_goose_game load on the other paths and not on this one."
    )


@pytest.mark.parametrize("script", LOADING_SCRIPTS)
def test_no_script_keeps_its_own_copy(script):
    assert "_permissive_choice_meta_new" not in _defines(script), f"{script} still carries its own copy"
    assert "derive_host_gate_settings" not in _defines(script), f"{script} still carries its own copy"


@pytest.mark.parametrize("script", GENERATING_SCRIPTS)
def test_every_generating_script_opens_the_host_gates(script):
    assert "apply_host_gates" in _calls(script), (
        f"{script} runs Generate without the host permission gates: a seed that generated in "
        "production fails here."
    )


# ─── The permissive Choice metaclass ───────────────────────────────────────────


def _fake_options_module():
    """A Choice whose metaclass asserts on `option_random*`, as Archipelago's does."""

    class ChoiceMeta(type):
        def __new__(mcs, name, bases, namespace, **kwargs):
            assert not any(k.startswith("option_random") for k in namespace), "random is reserved"
            return super().__new__(mcs, name, bases, namespace, **kwargs)

    class Choice(metaclass=ChoiceMeta):
        pass

    return types.SimpleNamespace(Choice=Choice), ChoiceMeta


def test_a_choice_defining_option_random_loads_without_it():
    module, meta = _fake_options_module()
    generation_setup.install_permissive_choice_meta(module)

    class Goal(module.Choice):
        option_a = 0
        option_random = 1

    assert not hasattr(Goal, "option_random")
    assert Goal.option_a == 0


def test_any_other_assertion_still_raises():
    module, meta = _fake_options_module()
    generation_setup.install_permissive_choice_meta(module)
    original = meta.__new__

    def strict(mcs, name, bases, namespace, **kwargs):
        raise AssertionError("duplicate option")

    meta.__new__ = strict
    try:
        generation_setup.install_permissive_choice_meta(module)
        with pytest.raises(AssertionError, match="duplicate"):
            class Broken(module.Choice):
                option_a = 0
    finally:
        meta.__new__ = original


def test_arming_twice_does_not_wrap_twice():
    """reachable.py and generate_multiworld.py may both run in one interpreter in tests."""
    module, meta = _fake_options_module()
    generation_setup.install_permissive_choice_meta(module)
    armed = meta.__new__
    generation_setup.install_permissive_choice_meta(module)

    assert meta.__new__ is armed


# ─── The host permission gates ─────────────────────────────────────────────────


def test_applying_the_gates_writes_host_yaml_and_drops_the_settings_cache(tmp_path, monkeypatch):
    """A world that read the settings before the gates were written would otherwise keep the
    cached copy, gates off, and Generate would raise as if nothing had been done."""
    written = {}
    monkeypatch.setattr(generation_setup, "derive_host_gate_settings", lambda types_: {"vs_options": {"allow_x": True}})
    monkeypatch.setattr(generation_setup, "write_host_gate_yaml", lambda gates, path: written.update(gates=gates, path=path))

    def get_settings():
        return None

    get_settings._cache = object()
    fake_settings = types.SimpleNamespace(get_settings=get_settings)
    monkeypatch.setitem(sys.modules, "settings", fake_settings)
    monkeypatch.setitem(sys.modules, "Utils", types.SimpleNamespace(user_path=lambda name: str(tmp_path / name)))

    generation_setup.apply_host_gates({})

    assert written == {"gates": {"vs_options": {"allow_x": True}}, "path": str(tmp_path / "host.yaml")}
    assert not hasattr(get_settings, "_cache")


def test_no_gate_to_open_leaves_everything_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(generation_setup, "derive_host_gate_settings", lambda types_: {})

    def get_settings():
        return None

    get_settings._cache = sentinel = object()
    monkeypatch.setitem(sys.modules, "settings", types.SimpleNamespace(get_settings=get_settings))
    monkeypatch.setitem(sys.modules, "Utils", types.SimpleNamespace(user_path=lambda name: str(tmp_path / name)))

    generation_setup.apply_host_gates({})

    assert get_settings._cache is sentinel
    assert not (tmp_path / "host.yaml").exists()


def test_the_gates_are_merged_into_an_existing_host_yaml(tmp_path):
    host = tmp_path / "host.yaml"
    host.write_text("server_options:\n  port: 38281\nvs_options:\n  keep: 1\n", encoding="utf-8")

    generation_setup.write_host_gate_yaml({"vs_options": {"allow_x": True}}, str(host))

    import yaml

    data = yaml.safe_load(host.read_text(encoding="utf-8"))
    assert data == {"server_options": {"port": 38281}, "vs_options": {"keep": 1, "allow_x": True}}
