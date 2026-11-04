"""The reachable host runs one fixed command: a request only chooses paths of the session volume."""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from reachable_host import daemon_command  # noqa: E402


def _request(**fields: object) -> bytes:
    return json.dumps(fields).encode() + b"\n"


def test_a_request_starts_the_session_daemon() -> None:
    command = daemon_command(_request(archipelago="/data/output/AP_1.zip", yamls="/data/yamls"))

    assert command[1:] == [
        "/reachable/reachable.py",
        "--archipelago", "/data/output/AP_1.zip",
        "--yamls", "/data/yamls",
        "--daemon",
        "--session",
    ]


@pytest.mark.parametrize("path", ["/etc/passwd", "/data/../etc/passwd", "/data", "relative", "", None, 3])
def test_a_path_outside_the_volume_is_refused(path: object) -> None:
    with pytest.raises(ValueError):
        daemon_command(_request(archipelago=path, yamls="/data/yamls"))


def test_a_request_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(ValueError):
        daemon_command(b"[]\n")


def test_garbage_is_refused() -> None:
    with pytest.raises(ValueError):
        daemon_command(b"not json\n")
