"""The introspection's stdout holds its JSON and nothing else (story 38.31).

The Chrono Trigger Dalton apworld prints "C compression module not found.  Falling back to python." when it is
imported. That line landed in front of the JSON, the orchestrator stored stdout as-is, and the locations could no
longer be read: forcing the version answered "runner unavailable".
"""
from __future__ import annotations

import ast
import os

_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "introspect_options.py")


def _tree() -> ast.Module:
    return ast.parse(open(_SCRIPT, encoding="utf-8").read())


def _is_stdout_redirect(node: ast.stmt) -> bool:
    """`_JSON_OUT = reserve_stdout_for_json()`."""
    return (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and ast.unparse(node.targets[0]) == "_JSON_OUT"
        and ast.unparse(node.value) == "reserve_stdout_for_json()"
    )


def _prints(tree: ast.Module) -> list[ast.Call]:
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"
    ]


def test_stdout_goes_to_stderr_before_any_world_is_imported() -> None:
    body = _tree().body
    redirect = next(i for i, node in enumerate(body) if _is_stdout_redirect(node))
    first_project_import = next(
        i for i, node in enumerate(body)
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in {"worlds", "Utils"}
    )

    assert redirect < first_project_import


def test_only_the_json_reaches_the_real_stdout() -> None:
    to_real_stdout = [
        call for call in _prints(_tree())
        if any(kw.arg == "file" and ast.unparse(kw.value) == "_JSON_OUT" for kw in call.keywords)
    ]

    assert len(to_real_stdout) == 1
    assert ast.unparse(to_real_stdout[0].args[0]).startswith("json.dumps(")


_CHATTY = r"""
import os, subprocess, sys
sys.path.insert(0, sys.argv[1])
from generation_setup import reserve_stdout_for_json
out = reserve_stdout_for_json()
print("A print")
sys.__stdout__.write("B dunder stdout\\n"); sys.__stdout__.flush()
os.write(1, b"C descriptor\\n")
subprocess.run([sys.executable, "-c", "print('D subprocess')"])
print('{"options": {}}', file=out)
out.flush()
"""


def test_whatever_a_world_writes_to_stdout_goes_to_stderr() -> None:
    """Cookie Clicker and others wrote past `sys.stdout`: `sys.__stdout__`, descriptor 1, a subprocess."""
    import subprocess
    import sys

    repo = os.path.dirname(_SCRIPT)
    done = subprocess.run([sys.executable, "-c", _CHATTY, repo], capture_output=True, text=True, check=True)

    assert done.stdout == '{"options": {}}\n'
    for line in ("A print", "B dunder stdout", "C descriptor", "D subprocess"):
        assert line in done.stderr
