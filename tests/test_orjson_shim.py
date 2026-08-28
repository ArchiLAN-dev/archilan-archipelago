"""The orjson bootstrap must be the same in every script that imports apworlds.

A world that *generates* must also *introspect*: both scripts load the same third-party code the
same way, and any divergence means a world works on one path and not the other.

That is exactly what happened. `generate_multiworld.py` preferred the real library and exposed the
`.orjson` submodule the real package carries; `introspect_options.py` replaced the module with a
stub that had neither. `clair_obscur` does `from orjson import orjson`, so it generated fine for
months while its option types were never introspected at all - silently, because introspection runs
in a background goroutine at upload and nobody reads its stderr.

The behavioural tests execute each script's bootstrap for real rather than reading it: what matters
is the module it leaves behind, not the shape of the source.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPTS = ["introspect_options.py", "generate_multiworld.py"]

# The shim, from its `try:` to the `.orjson` self-reference that closes it. Anchoring on the code
# itself rather than on neighbouring comments keeps the extraction honest if the files move around.
_SHIM = re.compile(
    r"try:\s*\n\s*import orjson as _orjson.*?_orjson\.orjson = _orjson[^\n]*",
    re.DOTALL,
)


def _bootstrap(script: str) -> str:
    source = open(os.path.join(_REPO_ROOT, script), encoding="utf-8").read()
    match = _SHIM.search(source)
    assert match is not None, f"no orjson bootstrap found in {script}"

    return match.group(0)


@pytest.mark.parametrize("script", SCRIPTS)
def test_from_orjson_import_orjson_works(script: str) -> None:
    """`from orjson import orjson` must resolve even when the real library is absent.

    The real package exposes its native extension as a submodule of that name, and a world written
    against it imports it that way. A fallback without it turns a missing optional dependency into
    an unimportable world.
    """
    # Forcing the ImportError is what puts the fallback - the interesting half - under test.
    shim = _bootstrap(script).replace("import orjson as _orjson", "raise ImportError('forced')")

    program = (
        "import json as _json\n"
        "import sys, types\n"
        + shim
        + "\nfrom orjson import orjson\n"
        "assert orjson.loads('{\"a\": 1}') == {'a': 1}\n"
        "assert orjson.dumps({'a': 1}) == b'{\"a\": 1}'\n"
        "print('ok')\n"
    )

    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_the_two_scripts_agree() -> None:
    """The invariant the drift broke, stated directly."""
    normalised = [re.sub(r"\s+|#.*", "", _bootstrap(s)) for s in SCRIPTS]

    assert normalised[0] == normalised[1], (
        "introspect_options.py and generate_multiworld.py must bootstrap orjson identically; "
        "a world that generates has to introspect too."
    )
