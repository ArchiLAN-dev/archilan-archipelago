"""No script of this image deserialises with plain pickle.

A save or a multidata can come from an archive a member imported, and ``pickle`` runs code while
loading. Everything goes through ``Utils.restricted_loads``, the allowlist MultiServer itself uses.
"""
from __future__ import annotations

import ast
import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _raw_pickle_calls(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        f"{path.name}:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in {"pickle", "cPickle", "_pickle"}
        and node.func.attr in {"load", "loads", "Unpickler"}
    ]


def test_no_script_uses_plain_pickle() -> None:
    offenders = [call for path in sorted(_REPO_ROOT.glob("*.py")) for call in _raw_pickle_calls(path)]

    assert offenders == []
