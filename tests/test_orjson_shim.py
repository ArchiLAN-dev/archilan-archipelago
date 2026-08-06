"""Regression tests for the orjson stub/shim shared by the AP entry scripts.

Reproduces the clair_obscur failure class: an apworld doing ``from orjson import orjson``
at import time. reachable.py used to pre-install a bare orjson stub exposing only
loads/dumps, so that import raised ``cannot import name 'orjson' from 'orjson'``, the
world was never registered, and the reachability re-generation died with
``No world found to handle game Clair Obscur Expedition 33`` for every slot.

The scripts cannot be imported outside the AP container (they import AP core at module
level), so each test extracts the script's real top-level orjson block with ast and runs
it in a subprocess where the real orjson package is blocked - exercising the shim branch
exactly as written in the script under test.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SCRIPTS = (
    "reachable.py",
    "generate_multiworld.py",
    "generate_template.py",
    "introspect_options.py",
)

# Runs before the extracted block: provide its dependencies (sys/types/_json) and block
# the real orjson so the shim fallback branch is what gets exercised, even on machines
# where orjson happens to be installed.
_PRELUDE = (
    "import sys, types\n"
    "import json as _json\n"
    "class _BlockOrjson:\n"
    "    def find_spec(self, name, path=None, target=None):\n"
    "        if name == 'orjson':\n"
    "            raise ImportError('orjson blocked for test')\n"
    "        return None\n"
    "sys.meta_path.insert(0, _BlockOrjson())\n"
    "sys.modules.pop('orjson', None)\n"
)

# Runs after the extracted block: the exact import shape that broke clair_obscur, plus a
# sanity check that the shim still (de)serializes like the real package.
_ASSERTIONS = (
    "from orjson import orjson\n"
    "assert orjson.loads('{\"a\": 1}') == {'a': 1}\n"
    "assert isinstance(orjson.dumps({'a': 1}), bytes)\n"
)


def _orjson_block(script_name: str) -> str:
    """The script's top-level statements that mention orjson, verbatim."""
    path = os.path.join(_REPO_ROOT, script_name)
    with open(path, encoding="utf-8") as f:
        source = f.read()
    segments = [
        seg
        for node in ast.parse(source).body
        if (seg := ast.get_source_segment(source, node)) and "orjson" in seg
    ]
    assert segments, f"{script_name}: no top-level orjson block found"
    return "\n".join(segments)


def _run_shim(script_name: str) -> subprocess.CompletedProcess:
    program = _PRELUDE + _orjson_block(script_name) + "\n" + _ASSERTIONS
    return subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )


def test_shim_supports_from_orjson_import_orjson() -> None:
    for script in _SCRIPTS:
        proc = _run_shim(script)
        assert proc.returncode == 0, f"{script}: {proc.stderr}"


if __name__ == "__main__":
    test_shim_supports_from_orjson_import_orjson()
    print("all orjson shim regression tests passed")
