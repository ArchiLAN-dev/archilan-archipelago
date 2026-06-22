"""Regression tests for the reachability JSON-protocol stdout isolation (protocol_io).

Reproduces the Simpsons Hit and Run failure class: while reachable.py runs AP world
generation, AP core / a third-party apworld may print() to stdout (e.g. the Simpsons Hit and
Run apworld prints "Getting UT slot data."). Before the fix, that stray line landed on the
protocol stdout and the bridge parsed it as the ready/result line, failing with
``Expecting value: line 1 column 1 (char 0)`` returned to the client.

These run protocol_io in a subprocess so the child's stdout/stderr can be inspected
separately from the test runner's own streams (isolate_stdout() rebinds sys.stdout).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Child program: isolate stdout, emit two protocol messages, and deliberately pollute stdout
# the way AP / an apworld would. Only the emit()-ed lines may reach the real stdout.
_HARNESS = (
    "import sys\n"
    "import protocol_io\n"
    "protocol_io.isolate_stdout()\n"
    # Simulate AP core / apworld output during world generation.
    "print('Getting UT slot data.')\n"
    "sys.stdout.write('more apworld noise\\n')\n"
    # The only writes that may reach the real stdout.
    "protocol_io.emit({'ready': True})\n"
    "protocol_io.emit({'counts': {'reachable_now': 3}})\n"
)


def _run_harness() -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=_REPO_ROOT)
    return subprocess.run(
        [sys.executable, "-c", _HARNESS],
        capture_output=True,
        text=True,
        env=env,
        cwd=_REPO_ROOT,
        check=True,
    )


def test_stdout_contains_only_protocol_json_lines() -> None:
    proc = _run_harness()
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 2, f"expected exactly 2 protocol lines, got {lines!r}"
    assert json.loads(lines[0]) == {"ready": True}
    assert json.loads(lines[1]) == {"counts": {"reachable_now": 3}}


def test_apworld_prints_are_diverted_to_stderr() -> None:
    proc = _run_harness()
    # The polluting prints must NOT be on stdout (they would corrupt the protocol)...
    assert "Getting UT slot data." not in proc.stdout
    assert "noise" not in proc.stdout
    # ...and must land on stderr, which the bridge's exec-stream frame demux ignores.
    assert "Getting UT slot data." in proc.stderr


def test_first_stdout_line_parses_as_json() -> None:
    # Directly asserts the original failure mode is gone: json.loads of the first stdout line
    # previously raised "Expecting value: line 1 column 1 (char 0)".
    proc = _run_harness()
    first = proc.stdout.splitlines()[0]
    json.loads(first)  # must not raise


def test_emit_before_isolate_raises() -> None:
    # emit() on an unisolated stdout is the very bug this module prevents; it must fail loudly.
    program = "import protocol_io; protocol_io.emit({'x': 1})\n"
    env = dict(os.environ, PYTHONPATH=_REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        env=env,
        cwd=_REPO_ROOT,
    )
    assert proc.returncode != 0
    assert "isolate_stdout() must be called before emit()" in proc.stderr


if __name__ == "__main__":
    test_stdout_contains_only_protocol_json_lines()
    test_apworld_prints_are_diverted_to_stderr()
    test_first_stdout_line_parses_as_json()
    test_emit_before_isolate_raises()
    print("all protocol_io regression tests passed")