"""Regression tests for the retry rollback in apworld_import.import_world.

Reproduces the Minecraft Dig failure class: a world that needs a stub round (it imports a
client-only module at module level) is imported twice - once honestly, once with the stub in
place. Archipelago fills its registries from class bodies, so the second execution used to
collide with what the first one left behind::

    Warning: failed to load <hash>.apworld (minecraft_dig): Two auto patch containers are
    using the same file extension: <class 'worlds.minecraft_dig.MinecraftDigPatch.
    MinecraftDigProcedurePatch'>, <class 'worlds.minecraft_dig.MinecraftDigPatch.
    MinecraftDigProcedurePatch'>

...and the seed then died on "No world found to handle game Minecraft Dig". The leak only
bites when worlds.Files (or BaseClasses) survives the rollback, i.e. when an earlier world
already imported it - which is why the world generated fine alone and broke in a multiworld.

The harness fakes the two Archipelago registries involved rather than downloading the AP
source: what is under test is the rollback, not AP itself.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Faithful stand-ins for worlds.Files.AutoPatchRegister and BaseClasses.CollectionState:
# both raise when the same class registers twice, exactly like Archipelago 0.6.
_FAKE_FILES = textwrap.dedent(
    """
    class AutoPatchRegister(type):
        patch_types = {}
        file_endings = {}

        def __new__(mcs, name, bases, dct):
            new_class = super().__new__(mcs, name, bases, dct)
            if "game" in dct:
                ending = dct["patch_file_ending"]
                if ending in AutoPatchRegister.file_endings:
                    raise Exception(
                        "Two auto patch containers are using the same file extension: "
                        f"{AutoPatchRegister.file_endings[ending]}, {new_class}")
                AutoPatchRegister.patch_types[dct["game"]] = new_class
                AutoPatchRegister.file_endings[ending] = new_class
            return new_class
    """
)

_FAKE_BASE_CLASSES = textwrap.dedent(
    """
    class CollectionState:
        additional_init_functions = []
        additional_copy_functions = []


    def register_mixin(name, function):
        if hasattr(CollectionState, name):
            raise Exception(f"Name conflict on Logic Mixin trying to overwrite {name}")
        setattr(CollectionState, name, function)
        CollectionState.additional_init_functions.append(function)
    """
)

# A world that registers a patch container and a logic mixin, then trips on a client-only
# module. import_world stubs the missing module and retries - re-running everything above.
_FLAKY_WORLD = textwrap.dedent(
    """
    from worlds.Files import AutoPatchRegister
    import BaseClasses


    class FlakyProcedurePatch(metaclass=AutoPatchRegister):
        game = "Flaky Game"
        patch_file_ending = ".apflaky"


    BaseClasses.register_mixin("_flaky_has_gem", lambda self: True)

    import flaky_client_only_dependency  # noqa: F401  - stubbed on the retry
    """
)

# A world tripping on a module that EXISTS but cannot be loaded. Stand-in for _tkinter,
# which ships in the image without its libtk8.6.so: the import does not fail because Python
# cannot find the module, it fails while loading it, so a stub finder sitting at the end of
# sys.meta_path is never reached and the retry hits the very same error.
_BROKEN_DEPENDENCY = textwrap.dedent(
    """
    raise ImportError("libfake8.6.so: cannot open shared object file: No such file or "
                      "directory", name="broken_native_dependency")
    """
)

_TKINTER_LIKE_WORLD = textwrap.dedent(
    """
    import broken_native_dependency  # noqa: F401  - present on disk, unloadable

    GAME = "Tk World"
    """
)

# Same shape, but the failure is not an ImportError and no stubbing can rescue it.
_BROKEN_WORLD = textwrap.dedent(
    """
    from worlds.Files import AutoPatchRegister


    class BrokenProcedurePatch(metaclass=AutoPatchRegister):
        game = "Broken Game"
        patch_file_ending = ".apbroken"


    raise ValueError("world is broken")
    """
)

# Faithful stand-ins for worlds.AutoWorld.AutoWorldRegister and Utils.tuplize_version. The
# manifest pass reaches for both, and tuplize_version accepts nothing but "major.minor.build" -
# which is the whole point of the test below.
_FAKE_AUTO_WORLD = textwrap.dedent(
    """
    class AutoWorldRegister:
        world_types = {}
    """
)

_FAKE_UTILS = textwrap.dedent(
    """
    import typing


    class Version(typing.NamedTuple):
        major: int
        minor: int
        build: int


    def tuplize_version(version):
        return Version(*(int(piece) for piece in version.split(".")))
    """
)

# A world that registers itself, like a class body does through the real metaclass.
_MANIFEST_WORLD = textwrap.dedent(
    """
    from worlds.AutoWorld import AutoWorldRegister


    class ManifestWorld:
        game = "Manifest Game"
        world_version = (0, 0, 0)


    AutoWorldRegister.world_types["Manifest Game"] = ManifestWorld
    """
)

_HARNESS = textwrap.dedent(
    """
    import json, sys, types

    repo_root, tmp_dir, world_source = sys.argv[1], sys.argv[2], sys.argv[3]
    sys.path.insert(0, repo_root)
    # The real loaders expose the extracted apworld root on sys.path for its bundled deps.
    sys.path.insert(0, tmp_dir)

    # Pre-load the modules holding the registries, as an earlier world would have done.
    # Surviving the rollback is precisely what makes their leftovers dangerous.
    base_classes = types.ModuleType("BaseClasses")
    exec(open(sys.argv[4], encoding="utf-8").read(), base_classes.__dict__)
    sys.modules["BaseClasses"] = base_classes

    files = types.ModuleType("worlds.Files")
    exec(open(sys.argv[5], encoding="utf-8").read(), files.__dict__)

    auto_world = types.ModuleType("worlds.AutoWorld")
    exec(open(sys.argv[6], encoding="utf-8").read(), auto_world.__dict__)

    utils = types.ModuleType("Utils")
    exec(open(sys.argv[7], encoding="utf-8").read(), utils.__dict__)
    sys.modules["Utils"] = utils

    worlds = types.ModuleType("worlds")
    worlds.__path__ = [tmp_dir]
    worlds.__package__ = "worlds"
    worlds.Files = files
    worlds.AutoWorld = auto_world
    sys.modules["worlds"] = worlds
    sys.modules["worlds.Files"] = files
    sys.modules["worlds.AutoWorld"] = auto_world

    from apworld_import import import_world

    error = None
    try:
        import_world(world_source)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    print(json.dumps({
        "error": error,
        "file_endings": sorted(files.AutoPatchRegister.file_endings),
        "patch_types": sorted(files.AutoPatchRegister.patch_types),
        "mixin_attrs": sorted(n for n in vars(base_classes.CollectionState) if "flaky" in n),
        "init_functions": len(base_classes.CollectionState.additional_init_functions),
        "world_types": sorted(auto_world.AutoWorldRegister.world_types),
        "world_versions": {
            game: list(cls.world_version)
            for game, cls in auto_world.AutoWorldRegister.world_types.items()
        },
    }))
    """
)


def _run(
    tmp_path,
    world_module: str,
    world_body: str,
    extra_modules: dict | None = None,
    manifest: dict | None = None,
) -> dict:
    """Import `world_module` through import_world in a subprocess, return the registry state."""
    package_dir = tmp_path / world_module.split(".")[-1]
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text(world_body, encoding="utf-8")
    if manifest is not None:
        (package_dir / "archipelago.json").write_text(json.dumps(manifest), encoding="utf-8")
    for name, body in (extra_modules or {}).items():
        (tmp_path / f"{name}.py").write_text(body, encoding="utf-8")

    base_classes = tmp_path / "_fake_base_classes.py"
    base_classes.write_text(_FAKE_BASE_CLASSES, encoding="utf-8")
    files = tmp_path / "_fake_files.py"
    files.write_text(_FAKE_FILES, encoding="utf-8")
    auto_world = tmp_path / "_fake_auto_world.py"
    auto_world.write_text(_FAKE_AUTO_WORLD, encoding="utf-8")
    utils = tmp_path / "_fake_utils.py"
    utils.write_text(_FAKE_UTILS, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-c", _HARNESS, _REPO_ROOT, str(tmp_path), world_module,
         str(base_classes), str(files), str(auto_world), str(utils)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"harness crashed:\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_stub_retry_does_not_collide_with_its_own_leftovers(tmp_path):
    """The reported bug: a world needing a stub round must still load, once, cleanly."""
    state = _run(tmp_path, "worlds.flaky_world", _FLAKY_WORLD)

    assert state["error"] is None
    assert state["file_endings"] == [".apflaky"]
    assert state["patch_types"] == ["Flaky Game"]
    # The logic mixin grafted itself onto CollectionState exactly once.
    assert state["mixin_attrs"] == ["_flaky_has_gem"]
    assert state["init_functions"] == 1


def test_dependency_that_exists_but_cannot_load_is_stubbed(tmp_path):
    """The _tkinter case: unloadable is as stubbable as absent, or the world is lost."""
    state = _run(
        tmp_path,
        "worlds.tk_world",
        _TKINTER_LIKE_WORLD,
        extra_modules={"broken_native_dependency": _BROKEN_DEPENDENCY},
    )

    assert state["error"] is None


def test_non_import_failure_leaves_the_registries_untouched(tmp_path):
    """A world that dies on something stubbing cannot fix must not poison later worlds."""
    state = _run(tmp_path, "worlds.broken_world", _BROKEN_WORLD)

    assert state["error"] == "ValueError: world is broken"
    assert state["file_endings"] == []
    assert state["patch_types"] == []
def test_manifest_names_the_world_version(tmp_path):
    """import_module alone leaves world_version at its default: the manifest pass fills it in.

    The core applies it in a pass that only walks its own worlds/ and custom_worlds/ folders, so an
    apworld extracted elsewhere - which is exactly how the loaders here take them - never gets one.
    Generate.py then rejects any yaml carrying a `version: {min: ...}` requirement, because 0.0.0 is
    below everything.
    """
    state = _run(
        tmp_path,
        "worlds.manifest_world",
        _MANIFEST_WORLD,
        manifest={"game": "Manifest Game", "world_version": "1.2.3"},
    )

    assert state["error"] is None
    assert state["world_types"] == ["Manifest Game"]
    assert state["world_versions"]["Manifest Game"] == [1, 2, 3]


def test_a_broken_manifest_leaves_the_world_loaded(tmp_path):
    """A version label must never be able to unload a world.

    `tuplize_version` accepts nothing but "major.minor.build": a two-part version, an int, a
    "-beta" suffix or a malformed archipelago.json all raise. Applied inside import_world's guarded
    block, that rolled the world back and re-raised - and the loaders skip a world whose import
    raised, so a third-party version string could turn into "No world found to handle game X".
    """
    state = _run(
        tmp_path,
        "worlds.manifest_world",
        _MANIFEST_WORLD,
        manifest={"game": "Manifest Game", "world_version": "1.2"},
    )

    assert state["error"] is None
    assert state["world_types"] == ["Manifest Game"]
    # Applying it failed, so the default stands - the world itself is untouched.
    assert state["world_versions"]["Manifest Game"] == [0, 0, 0]


def test_a_world_without_a_manifest_needs_no_registry(tmp_path):
    """No manifest, no lookup: the pass must not drag worlds.AutoWorld into every import."""
    state = _run(tmp_path, "worlds.flaky_world", _FLAKY_WORLD)

    assert state["error"] is None
    assert state["world_versions"] == {}
