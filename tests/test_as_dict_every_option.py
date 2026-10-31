"""`options.as_dict()` of every option, as the Launcher allows (story 38.25).

`CommonOptions.as_dict` asserts "Specify only options you need" when asked for every option. The Windows Launcher
runs optimized and skips that assert: Caves of Qud puts all its options in slot data and generates there, while
our image failed at the very end of the generation.
"""
from __future__ import annotations

import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

import generation_setup  # noqa: E402


class Value:
    def __init__(self, value):
        self.value = value


def _options_cls():
    class FakeOptions:
        type_hints = {"goal": int, "death_link": int, "exclude": set}

        def __init__(self):
            self.goal = Value(2)
            self.death_link = Value(1)
            self.exclude = Value({"b", "a"})

        # The checks and the result of Archipelago's CommonOptions.as_dict (0.6.8), casing aside.
        def as_dict(self, *option_names, casing="snake", toggles_as_bools=False):
            assert option_names, "options.as_dict() was used without any option names."
            assert len(option_names) < len(self.__class__.type_hints), "Specify only options you need."
            result = {}
            for name in option_names:
                if name not in type(self).type_hints:
                    raise ValueError(f"{name} not found")
                value = getattr(self, name).value
                if isinstance(value, set):
                    value = sorted(value)
                elif toggles_as_bools and type(self).type_hints[name] is int:
                    value = bool(value)
                result[name if casing == "snake" else name.upper()] = value
            return result

    return FakeOptions


def test_every_option_gives_what_the_launcher_gives():
    cls = _options_cls()
    with pytest.raises(AssertionError, match="Specify only options you need"):
        cls().as_dict("goal", "death_link", "exclude")

    generation_setup.allow_as_dict_of_every_option(cls)

    result = cls().as_dict(*cls.type_hints)
    assert result == {"goal": 2, "death_link": 1, "exclude": ["a", "b"]}
    assert list(result) == ["goal", "death_link", "exclude"]
    assert cls().as_dict("exclude", "goal", "death_link", casing="upper", toggles_as_bools=True) == {
        "EXCLUDE": ["a", "b"], "GOAL": True, "DEATH_LINK": True,
    }


def test_other_calls_keep_their_behaviour():
    cls = _options_cls()
    generation_setup.allow_as_dict_of_every_option(cls)

    assert cls().as_dict("goal") == {"goal": 2}
    with pytest.raises(AssertionError, match="without any option names"):
        cls().as_dict()
    with pytest.raises(ValueError, match="nope not found"):
        cls().as_dict("goal", "death_link", "nope")


def test_installing_twice_wraps_once():
    cls = _options_cls()
    generation_setup.allow_as_dict_of_every_option(cls)
    wrapped = cls.as_dict
    generation_setup.allow_as_dict_of_every_option(cls)

    assert cls.as_dict is wrapped
