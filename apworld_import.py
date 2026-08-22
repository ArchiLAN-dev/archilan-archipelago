"""Shared apworld import machinery: honest first, stub only what is truly missing.

Both generate_template.py and generate_multiworld.py used to install a catch-all import
finder that answered EVERY unknown module with a fake object, so that client-only C
extensions (dolphin_memory_engine, gclib, Pymem…) could not stop a world from loading
server-side.

That catch-all lies to the worlds that handle a missing dependency themselves. Castlevania
Aria of Sorrow ships its own vendored copy of pydantic and selects it with
``try: from pydantic.v1 import … / except ImportError: <vendored copy>``. Because the
catch-all answered the import, the ImportError never fired, the world got a stub as the
base class of its data models (``__mro_entries__`` returns ``object``, so the class loses
its ``__init__``) and died on ``TypeError: RoutingInfo() takes no arguments``.

So: import with no stubbing at all, exactly like desktop Archipelago, and only when the
import fails on a module this container cannot provide - absent, or present but unloadable
for want of a shared library - do we stub THAT module and retry. Every world
converges to its own minimal stub set, recomputed at load time - there is no list to
maintain, a world that ships a fallback takes it, and a new client-only dependency added
upstream tomorrow is handled without touching this file.

Retrying an import is only safe if the failed attempt left nothing behind: Archipelago's
registries are written from class bodies, so a second execution of the same module collides
with its own leftovers. See _REGISTRY_PATHS.
"""
import importlib
import importlib.abc
import importlib.machinery
import sys
import types
import json
import os

# Archipelago's own top-level modules. Stubbing one of these would produce a silently
# broken generator, so a failure on them stays fatal.
ARCHIP_ROOTS = frozenset({
    "BaseClasses",
    "entrance_rando",
    "Fill",
    "Generate",
    "Main",
    "MultiServer",
    "NetUtils",
    "Options",
    "Patch",
    "settings",
    "Utils",
    "WebHost",
    "worlds",
})

# Bound the retry loop: a world needing more than this many missing modules is broken
# beyond what stubbing can rescue.
MAX_STUB_ROUNDS = 12


class Stub:
    """Flexible no-op stub standing in for a module attribute that does not exist here."""

    def __getattr__(self, _n): return Stub()
    def __call__(self, *a, **kw): return Stub()
    def __mro_entries__(self, bases): return (object,)  # allow: class Foo(Stub()): …
    def __getitem__(self, key): return Stub()
    def __setitem__(self, key, value): pass
    def __delitem__(self, key): pass
    def __contains__(self, item): return False
    def __neg__(self): return Stub()
    def __pos__(self): return Stub()
    def __abs__(self): return Stub()
    def __invert__(self): return Stub()
    def __add__(self, o): return Stub()
    def __radd__(self, o): return Stub()
    def __sub__(self, o): return Stub()
    def __rsub__(self, o): return Stub()
    def __mul__(self, o): return Stub()
    def __rmul__(self, o): return Stub()
    def __truediv__(self, o): return Stub()
    def __rtruediv__(self, o): return Stub()
    def __floordiv__(self, o): return Stub()
    def __rfloordiv__(self, o): return Stub()
    def __mod__(self, o): return Stub()
    def __rmod__(self, o): return Stub()
    def __pow__(self, o, m=None): return Stub()
    def __rpow__(self, o): return Stub()
    def __matmul__(self, o): return Stub()
    def __rmatmul__(self, o): return Stub()
    def __and__(self, o): return Stub()
    def __rand__(self, o): return Stub()
    def __or__(self, o): return Stub()
    def __ror__(self, o): return Stub()
    def __xor__(self, o): return Stub()
    def __rxor__(self, o): return Stub()
    def __lshift__(self, o): return Stub()
    def __rlshift__(self, o): return Stub()
    def __rshift__(self, o): return Stub()
    def __rrshift__(self, o): return Stub()
    def __lt__(self, o): return False
    def __le__(self, o): return False
    def __gt__(self, o): return False
    def __ge__(self, o): return False
    def __eq__(self, o): return isinstance(o, Stub)
    def __ne__(self, o): return not isinstance(o, Stub)
    def __bool__(self): return False
    def __int__(self): return 0
    def __float__(self): return 0.0
    def __complex__(self): return 0j
    def __index__(self): return 0
    def __str__(self): return ""
    def __repr__(self): return "stub"
    def __bytes__(self): return b""
    def __hash__(self): return 0
    def __iter__(self): return iter([])
    def __len__(self): return 0
    def items(self): return {}.items()
    def values(self): return {}.values()
    def keys(self): return {}.keys()


class OnDemandStubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Stubs only the module roots the retry loop proved unimportable, and only when enabled.

    Sits at the FRONT of sys.meta_path, but answers exclusively for roots already in
    `stubbed` - and a root only lands there after a real import of it raised ImportError.
    So nothing importable is ever shadowed.

    The front position is what makes "missing" cover more than "absent". A module can exist
    on disk and still be unimportable: _tkinter ships in the image but its shared library
    does not, so importing it raises "libtk8.6.so: cannot open shared object file". From the
    end of sys.meta_path the stub was unreachable - PathFinder found the real .so on every
    retry, the same ImportError came back, and the world was dropped even though nothing
    server-side needed the GUI toolkit at all.
    """

    def __init__(self):
        self.stubbed = set()
        self.enabled = True

    def find_spec(self, fullname, path, target=None):
        if not self.enabled:
            return None
        if fullname.split(".")[0] in self.stubbed:
            return importlib.machinery.ModuleSpec(fullname, self)
        return None

    def create_module(self, spec):
        return types.ModuleType(spec.name)

    def exec_module(self, module):
        module.__getattr__ = lambda _n: Stub()


finder = OnDemandStubFinder()
sys.meta_path.insert(0, finder)


# Archipelago registers a world's classes as a side effect of *executing* its module: the
# class body of a World, of a patch container or of a logic mixin writes itself into a
# global registry the moment it runs. A failed attempt therefore leaves entries behind, and
# because the retry re-executes those very class bodies it collides with its own leftovers:
# AutoPatchRegister raises "Two auto patch containers are using the same file extension",
# AutoLogicRegister raises "Name conflict on Logic Mixin", AutoWorldRegister raises
# "already registered". None of those is an ImportError, so the world dies on the retry for
# a reason that has nothing to do with the dependency that made it stumble.
#
# Only a registry reachable through a module that *survives* the rollback can leak: whatever
# was imported during the attempt is dropped from sys.modules and takes its registries with
# it. Hence the confusing symptom this guards against - a world loads fine on its own (it
# imported worlds.Files itself, so its leftovers died with the module) and fails as soon as
# another patching world loaded worlds.Files before it.
_REGISTRY_PATHS = (
    ("worlds", "AutoWorldRegister", "world_types"),
    ("worlds.AutoWorld", "AutoWorldRegister", "world_types"),
    ("worlds.Files", "AutoPatchRegister", "patch_types"),
    ("worlds.Files", "AutoPatchRegister", "file_endings"),
    ("worlds.Files", "AutoPatchExtensionRegister", "extension_types"),
    ("worlds.LauncherComponents", None, "components"),
    ("BaseClasses", "CollectionState", "additional_init_functions"),
    ("BaseClasses", "CollectionState", "additional_copy_functions"),
)


def _registry(module_name, owner_attr, registry_attr):
    """The live dict/list behind a (module, class, attribute) path, or None if not loaded."""
    owner = sys.modules.get(module_name)
    if owner is not None and owner_attr is not None:
        owner = getattr(owner, owner_attr, None)
    container = getattr(owner, registry_attr, None)
    return container if isinstance(container, (dict, list)) else None


def _collection_state():
    """BaseClasses.CollectionState, onto which logic mixins graft their methods."""
    return getattr(sys.modules.get("BaseClasses"), "CollectionState", None)


def _snapshot():
    """Capture what an import attempt may mutate, so a failure can be undone completely."""
    containers, seen = [], set()
    for path in _REGISTRY_PATHS:
        container = _registry(*path)
        # The same dict is reachable through several paths (worlds/worlds.AutoWorld).
        if container is not None and id(container) not in seen:
            seen.add(id(container))
            containers.append((container, container.copy()))
    state = _collection_state()
    return set(sys.modules), containers, state, None if state is None else set(vars(state))


def _rollback(snapshot):
    """Undo everything a failed import attempt left behind before retrying."""
    module_names, containers, state, state_attrs = snapshot
    for name in [n for n in sys.modules if n not in module_names]:
        del sys.modules[name]
    for container, saved in containers:
        container.clear()
        if isinstance(container, dict):
            container.update(saved)
        else:
            container.extend(saved)
    if state_attrs is not None:
        for name in set(vars(state)) - state_attrs:
            delattr(state, name)


def import_world(module_name, on_stub=None):
    """Import `module_name`, stubbing genuinely missing modules one at a time.

    The first attempt runs with stubbing fully disabled, whatever previous worlds needed,
    so a world that ships its own fallback always gets a truthful ImportError. `on_stub`
    is called with each module name stubbed, for reporting.
    """
    honest = True
    for _ in range(MAX_STUB_ROUNDS):
        snapshot = _snapshot()

        finder.enabled = not honest
        try:
            module = importlib.import_module(module_name)
            # Outside the guarded block below on purpose: that one rolls the world back and
            # re-raises, and the callers skip a world whose import raised. A version label must
            # never be able to unload a world - the manifest is third-party data, and
            # tuplize_version accepts nothing but "major.minor.build".
            try:
                _apply_world_manifest(module)
            except Exception as exc:
                print(f"Note: manifest not applied for {module_name}: {exc}", file=sys.stderr)
            return module
        except ImportError as exc:
            missing = (getattr(exc, "name", None) or "").split(".")[0]
            _rollback(snapshot)

            if not missing or missing in ARCHIP_ROOTS:
                raise
            if missing in finder.stubbed:
                if not honest:
                    # Already stubbed and still failing: stubbing cannot rescue this world.
                    raise
                honest = False
                continue

            finder.stubbed.add(missing)
            honest = False
            if on_stub is not None:
                on_stub(missing)
        except Exception:
            # A world can also die on something that is not an ImportError (a broken class
            # body, a stubbed value used at import time). Undo its half-registration too,
            # so one unloadable world cannot poison the ones loaded after it.
            _rollback(snapshot)
            raise
        finally:
            # Lazy imports performed later (at generation time) still get their stubs.
            finder.enabled = True

    raise ImportError(f"{module_name}: still missing modules after {MAX_STUB_ROUNDS} attempts")

def _apply_world_manifest(module):
    """Rejoue l'étape manifest du core (worlds/__init__.py).

    import_module n'exécute que le corps de classe : le world est enregistré mais
    world_version reste au défaut Version(0,0,0) (la metaclass interdit de le poser
    depuis la classe). Le core l'applique dans une passe séparée qui ne couvre que
    custom_worlds/ et worlds/, et seulement les worlds chargés sans erreur. Un world
    importé d'ailleurs - ou rescapé ici après un échec côté core - la rate. On la refait.
    """
    manifest = _read_manifest(module)
    game = manifest.get("game")
    if not game:
        # No manifest, nothing to name: leave without touching the registry, so a world that
        # ships none never depends on worlds.AutoWorld being importable here.
        return

    from worlds.AutoWorld import AutoWorldRegister
    from Utils import tuplize_version

    if game in AutoWorldRegister.world_types:
        cls = AutoWorldRegister.world_types[game]
        cls.world_version = tuplize_version(manifest.get("world_version", "0.0.0"))
        cls.manifest = manifest


def _read_manifest(module):
    """The world's archipelago.json, or an empty dict - same walk as the core's."""
    roots = list(getattr(module, "__path__", []))
    if not roots and getattr(module, "__file__", None):
        roots = [os.path.dirname(module.__file__)]

    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            if "archipelago.json" in files:
                with open(os.path.join(dirpath, "archipelago.json"), encoding="utf-8") as fh:
                    return json.load(fh)

    return {}
