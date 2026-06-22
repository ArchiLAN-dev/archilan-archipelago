#!/usr/bin/env python3
"""Stdout protocol isolation for headless Archipelago helper scripts.

These scripts speak a strict newline-delimited JSON protocol on stdout - one JSON object
per line. In particular reachable.py --daemon emits ``{"ready": true}`` then one result line
per request, and the bridge parses exactly one JSON line per message.

The catch: AP core and third-party apworlds print() freely to stdout during world generation
(e.g. the Simpsons Hit and Run apworld prints "Getting UT slot data."). Such a stray line
corrupts the protocol - the bridge reads it instead of the ready/result line, json.loads
fails, and the call surfaces to the client as
``{"error": "Expecting value: line 1 column 1 (char 0)"}``.

isolate_stdout() reserves the real stdout (a dup of fd 1) for protocol messages written via
emit(), and points sys.stdout at sys.stderr so any print() lands on stderr - which the
bridge's exec-stream frame demux ignores. Call isolate_stdout() once, as early as possible,
before importing AP or loading any apworld.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

_protocol_out = None  # the reserved real-stdout writer (set by isolate_stdout)


def isolate_stdout() -> None:
    """Reserve fd 1 for the JSON protocol and redirect sys.stdout to stderr. Idempotent."""
    global _protocol_out
    if _protocol_out is not None:
        return
    _protocol_out = os.fdopen(os.dup(1), "w", buffering=1)
    sys.stdout = sys.stderr


def emit(obj: Any) -> None:
    """Write one protocol JSON line to the reserved real stdout.

    Raises RuntimeError if isolate_stdout() has not been called - emitting on the unisolated
    stdout is exactly the bug this module prevents, so it must fail loudly rather than risk a
    polluted stream.
    """
    if _protocol_out is None:
        raise RuntimeError("isolate_stdout() must be called before emit()")
    _protocol_out.write(json.dumps(obj, ensure_ascii=False) + "\n")
    _protocol_out.flush()