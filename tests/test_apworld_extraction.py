r"""Extracting an apworld zipped on Windows (story 38.19).

An archive built on Windows names its entries `fez\__init__.py`. On Linux `ZipFile.extractall` only
converts `os.sep`, so it wrote flat files literally named `fez\__init__.py`: no `fez/` package, and
`import worlds.fez` failed. fez, dungeon_clawler and nrftw could be generated (the generation script
normalized each member) but never introspected, because introspect_options.py used `extractall`.

`_extract_apworld` lives in a script that does its work at import time, so it is lifted out of the
source like `_detect_pkg` in test_detect_pkg.py.
"""
from __future__ import annotations

import ast
import importlib
import os
import pathlib
import re
import sys
import zipfile

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_extract_apworld():
    source = pathlib.Path(os.path.join(_REPO_ROOT, "introspect_options.py")).read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == "_extract_apworld":
            namespace: dict = {"zipfile": zipfile}
            exec(compile(ast.Module(body=[node], type_ignores=[]), "<extract_apworld>", "exec"), namespace)

            return namespace["_extract_apworld"]

    raise AssertionError("_extract_apworld not found in introspect_options.py")


extract_apworld = _load_extract_apworld()


def _archive(path, entries: dict[str, str]) -> str:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)

    return str(path)


@pytest.mark.parametrize("sep", ["/", "\\"])
def test_the_package_is_extracted_as_a_directory(tmp_path, sep: str):
    apworld = _archive(tmp_path / "w.apworld", {
        f"fez_{ord(sep)}{sep}__init__.py": "from .options import GOAL\n",
        f"fez_{ord(sep)}{sep}options.py": "GOAL = 'cubes'\n",
        f"fez_{ord(sep)}{sep}data{sep}keys.json": "{}",
    })
    dest = tmp_path / "out"
    dest.mkdir()

    extract_apworld(apworld, str(dest))

    pkg = dest / f"fez_{ord(sep)}"
    assert (pkg / "__init__.py").is_file()
    assert (pkg / "data" / "keys.json").is_file()
    assert not any("\\" in name for name in os.listdir(dest))


def test_the_extracted_package_imports_with_its_relative_imports(tmp_path):
    # What actually failed in prod: the import, not the file listing.
    apworld = _archive(tmp_path / "w.apworld", {
        "winzipped_world\\__init__.py": "from .options import GOAL\n",
        "winzipped_world\\options.py": "GOAL = 'cubes'\n",
    })
    dest = tmp_path / "out"
    dest.mkdir()
    extract_apworld(apworld, str(dest))

    sys.path.insert(0, str(dest))
    try:
        module = importlib.import_module("winzipped_world")
        assert module.GOAL == "cubes"
    finally:
        sys.path.remove(str(dest))
        sys.modules.pop("winzipped_world", None)
        sys.modules.pop("winzipped_world.options", None)


@pytest.mark.parametrize("entry", ["..\\escaped.py", "\\rooted.py", "pkg\\..\\..\\escaped.py"])
def test_an_entry_cannot_land_outside_the_destination(tmp_path, entry: str):
    apworld = _archive(tmp_path / "w.apworld", {entry: "x = 1\n"})
    dest = tmp_path / "out"
    dest.mkdir()

    extract_apworld(apworld, str(dest))

    written = [os.path.join(root, f) for root, _, files in os.walk(tmp_path) for f in files]
    outside = [p for p in written if not p.endswith("w.apworld") and not p.startswith(str(dest))]
    assert outside == []


# ── Every script that extracts an apworld reads `\` as a separator ────────────
#
# The generation, template and reachability scripts already did; introspection did not, and three
# worlds silently lost their options, locations and items. The next `extractall` fails here.

_SCRIPTS = sorted(f for f in os.listdir(_REPO_ROOT) if f.endswith(".py"))


@pytest.mark.parametrize("script", _SCRIPTS)
def test_no_script_uses_extractall(script: str):
    source = pathlib.Path(os.path.join(_REPO_ROOT, script)).read_text(encoding="utf-8")

    assert ".extractall(" not in source


@pytest.mark.parametrize("script", _SCRIPTS)
def test_every_member_extraction_normalizes_the_separator(script: str):
    lines = pathlib.Path(os.path.join(_REPO_ROOT, script)).read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if re.search(r"\.extract\(", line):
            window = "\n".join(lines[max(0, i - 3):i])
            assert 'replace("\\\\", "/")' in window, f"{script}:{i + 1} extracts without normalizing \\"
