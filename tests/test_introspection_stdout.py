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
    """`sys.stdout = sys.stderr`."""
    return (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and ast.unparse(node.targets[0]) == "sys.stdout"
        and ast.unparse(node.value) == "sys.stderr"
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


def test_a_chatty_import_no_longer_reaches_stdout(capsys) -> None:
    """What the redirect does, on a print standing for the apworld's."""
    import sys

    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        print("C compression module not found.  Falling back to python.")
        print('{"options": {}}', file=real_stdout)
    finally:
        sys.stdout = real_stdout

    captured = capsys.readouterr()
    assert captured.out == '{"options": {}}\n'
    assert "C compression" in captured.err
