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
import fails on a genuinely missing module do we stub THAT module and retry. Every world
converges to its own minimal stub set, recomputed at load time - there is no list to
maintain, a world that ships a fallback takes it, and a new client-only dependency added
upstream tomorrow is handled without touching this file.
"""
import importlib
import importlib.abc
import importlib.machinery
import sys
import types

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
    """Stubs only the module roots the retry loop proved missing, and only when enabled.

    Appended to the END of sys.meta_path, so a module only reaches it when no real file
    was found anywhere.
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
sys.meta_path.append(finder)


def _world_types():
    """AutoWorldRegister.world_types once the worlds package exists, else None."""
    registry = getattr(sys.modules.get("worlds"), "AutoWorldRegister", None)
    return getattr(registry, "world_types", None)


def _rollback(module_names, world_games):
    """Undo everything a failed import attempt left behind before retrying."""
    for name in [n for n in sys.modules if n not in module_names]:
        del sys.modules[name]
    world_types = _world_types()
    if world_types is not None and world_games is not None:
        # A world class registers itself as soon as its class body runs, so a partial
        # import can leave a half-built world in the registry.
        for game in [g for g in world_types if g not in world_games]:
            del world_types[game]


def import_world(module_name, on_stub=None):
    """Import `module_name`, stubbing genuinely missing modules one at a time.

    The first attempt runs with stubbing fully disabled, whatever previous worlds needed,
    so a world that ships its own fallback always gets a truthful ImportError. `on_stub`
    is called with each module name stubbed, for reporting.
    """
    honest = True
    for _ in range(MAX_STUB_ROUNDS):
        module_names = set(sys.modules)
        world_types = _world_types()
        world_games = set(world_types) if world_types is not None else None

        finder.enabled = not honest
        try:
            return importlib.import_module(module_name)
        except ImportError as exc:
            missing = (getattr(exc, "name", None) or "").split(".")[0]
            _rollback(module_names, world_games)

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
        finally:
            # Lazy imports performed later (at generation time) still get their stubs.
            finder.enabled = True

    raise ImportError(f"{module_name}: still missing modules after {MAX_STUB_ROUNDS} attempts")
