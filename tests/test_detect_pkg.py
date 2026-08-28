"""Where an apworld's package sits inside its archive, and where that puts it on the import path.

The detection used to accept an `__init__.py` at exactly one level down, and to assume the archive
root was the directory to add to `worlds.__path__`. Three worlds - fez, dungeon_clawler, nrftw -
nest their package one level deeper. Detection fell through to "first entry's root": the *name* came
out right, the path did not, and `import worlds.fez` failed with ModuleNotFoundError against a
package that was sitting right there in the temp directory.

`_detect_pkg` is defined in a script that does its work at import time (argv, the Archipelago
source tree), so these tests execute the function's source in isolation rather than importing it.
"""
from __future__ import annotations

import ast
import os

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_detect_pkg():
    """`_detect_pkg` alone, lifted out of a module that cannot be imported here."""
    source = open(os.path.join(_REPO_ROOT, "introspect_options.py"), encoding="utf-8").read()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_detect_pkg":
            namespace: dict = {}
            exec(compile(ast.Module(body=[node], type_ignores=[]), "<detect_pkg>", "exec"), namespace)

            return namespace["_detect_pkg"]

    raise AssertionError("_detect_pkg not found in introspect_options.py")


detect_pkg = _load_detect_pkg()


def test_flat_layout_is_unchanged():
    # The common case, and the only one that used to work: package directly under the root.
    assert detect_pkg(["fez/__init__.py", "fez/Options.py"]) == ("fez", "")


def test_nested_layout_reports_its_parent():
    # The three failures. The name was never the problem - the parent was.
    assert detect_pkg(["fez/fez/__init__.py", "fez/fez/Options.py"]) == ("fez", "fez")


def test_a_worlds_prefixed_archive_resolves():
    assert detect_pkg(["worlds/nrftw/__init__.py"]) == ("nrftw", "worlds")


def test_the_shallowest_init_wins():
    # A world shipping sub-packages must resolve to its own root, not to one of them - and the
    # entries are deliberately listed worst-first.
    entries = ["fez/data/__init__.py", "fez/data/x.py", "fez/__init__.py"]

    assert detect_pkg(entries) == ("fez", "")


def test_directory_entries_do_not_confuse_it():
    # Zip files often carry explicit directory entries; they end in "/" and name no module.
    assert detect_pkg(["fez/", "fez/sub/", "fez/__init__.py"]) == ("fez", "")


def test_an_archive_without_any_init_falls_back_to_the_first_root():
    # Not importable as-is, but the old guess is kept rather than skipping the archive outright:
    # the loader below reports a real error instead of a silent absence.
    assert detect_pkg(["something/data.json", "something/x.txt"]) == ("something", "")


def test_an_empty_archive_detects_nothing():
    assert detect_pkg([]) is None


@pytest.mark.parametrize("sep", ["/", "\\"])
def test_both_path_separators_are_handled(sep: str):
    # Archives zipped on Windows can carry backslashes.
    assert detect_pkg([f"fez{sep}fez{sep}__init__.py"]) == ("fez", "fez")
