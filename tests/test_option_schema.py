"""What `option_schema` will and will not read out of an OptionDict's declared Schema.

The point of these tests is the *refusals*. Emitting a vocabulary that is merely plausible is
the failure mode that matters: it closes a dropdown on a value the world actually accepts, and
the player has no way to type around it. So every validator that is a program rather than a
list - a lambda, a Use, a bare type, a nested schema - must produce nothing at all.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

schema = pytest.importorskip("schema")

from option_schema import literal_value, schema_sub_values, values_from_validator  # noqa: E402


def _option(declared):
    """A stand-in for an OptionDict subclass carrying (or not carrying) a Schema."""
    return type("GameOptions", (), {"schema": declared} if declared is not None else {})


# ── What it reads ─────────────────────────────────────────────────────────────

def test_or_of_strings_is_a_vocabulary():
    cls = _option(schema.Schema({"battle_style": schema.Or("shift", "set")}))
    assert schema_sub_values(cls) == {"battle_style": {"values": ["shift", "set"]}}


def test_numbers_reach_the_wire_as_strings():
    # The editor round-trips dict values through text fields; `1` comes back as an int on the
    # way out (archipelago-yaml parses each entry), so the wire form is the string.
    cls = _option(schema.Schema({"text_frame": schema.Or(1, 2, 3)}))
    assert schema_sub_values(cls) == {"text_frame": {"values": ["1", "2", "3"]}}


def test_optional_key_still_names_a_sub_setting():
    cls = _option(schema.Schema({schema.Optional("sound"): schema.Or("stereo", "mono")}))
    assert schema_sub_values(cls) == {"sound": {"values": ["stereo", "mono"]}}


def test_duplicate_values_are_collapsed_in_order():
    cls = _option(schema.Schema({"k": schema.Or("a", "b", "a", "c")}))
    assert schema_sub_values(cls)["k"]["values"] == ["a", "b", "c"]


def test_nested_or_flattens():
    cls = _option(schema.Schema({"k": schema.Or(schema.Or("a", "b"), "c")}))
    assert schema_sub_values(cls)["k"]["values"] == ["a", "b", "c"]


def test_only_the_keys_that_declare_a_vocabulary_are_emitted():
    cls = _option(schema.Schema({
        "battle_style": schema.Or("shift", "set"),
        "player_name": str,
        "level": schema.And(int, lambda n: 1 <= n <= 100),
    }))
    assert schema_sub_values(cls) == {"battle_style": {"values": ["shift", "set"]}}


# ── What it refuses ───────────────────────────────────────────────────────────

def test_no_schema_emits_nothing():
    # The common case, Pokemon Platinum included: `default` and a docstring, nothing else.
    assert schema_sub_values(_option(None)) == {}


def test_a_partial_union_emits_nothing():
    # `Or("random", str)` accepts far more than "random". Emitting ["random"] would advertise a
    # closed list that is missing everything the `str` branch allows.
    cls = _option(schema.Schema({"k": schema.Or("random", str)}))
    assert schema_sub_values(cls) == {}


def test_a_lambda_emits_nothing():
    cls = _option(schema.Schema({"k": lambda v: v in ("a", "b")}))
    assert schema_sub_values(cls) == {}


def test_a_use_emits_nothing():
    cls = _option(schema.Schema({"k": schema.Use(str)}))
    assert schema_sub_values(cls) == {}


def test_a_bare_type_emits_nothing():
    cls = _option(schema.Schema({"k": str}))
    assert schema_sub_values(cls) == {}


def test_a_single_value_is_not_a_choice():
    cls = _option(schema.Schema({"k": "only"}))
    assert schema_sub_values(cls) == {}


def test_a_nested_schema_emits_nothing():
    cls = _option(schema.Schema({"k": schema.Schema({"deep": schema.Or("a", "b")})}))
    assert schema_sub_values(cls) == {}


def test_a_non_literal_key_is_skipped():
    cls = _option(schema.Schema({str: schema.Or("a", "b")}))
    assert schema_sub_values(cls) == {}


def test_a_schema_that_is_not_a_mapping_emits_nothing():
    assert schema_sub_values(_option(schema.Schema(str))) == {}


def test_a_schema_attribute_that_is_not_a_schema_emits_nothing():
    # A world is free to use the name `schema` for something else entirely.
    assert schema_sub_values(_option({"k": ["a", "b"]})) == {}


def test_booleans_are_left_to_the_free_text_field():
    # `Or(True, False)` on the wire would become "true"/"false" strings, and the dict serializer
    # quotes YAML 1.1 booleans - which is exactly how a bool turns into a string at generation.
    cls = _option(schema.Schema({"k": schema.Or(True, False)}))
    assert schema_sub_values(cls) == {}


def test_recursion_is_bounded():
    deep = schema.Or("a", "b")
    for _ in range(6):
        deep = schema.Schema(deep)
    assert values_from_validator(deep) is None


# ── literal_value ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "value,expected",
    [("shift", "shift"), (1, "1"), (-3, "-3"), (True, None), (False, None), ("", None),
     (None, None), (1.5, None), (["a"], None)],
)
def test_literal_value(value, expected):
    assert literal_value(value) == expected
