#!/usr/bin/env python3
"""Serves reachable.py to the session's bridge over a Unix socket of the session volume.

The socket lives on the volume, so only the containers of this session can reach it, and the bridge
needs no access to Docker to start the daemon. One connection is one `reachable.py --daemon`: the
first line names the seed, every byte after it is relayed to the daemon, and closing the connection
ends the daemon, which gives its memory back.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import posixpath
import sys

SOCKET_PATH = os.environ.get("REACHABLE_SOCKET", "/data/reachable.sock")
WORLDS_DIR = "/data/worlds"
SESSION_ROOT = "/data/"
# A daemon that ignores the end of its stdin (a stuck apworld) must not keep its memory.
_EXIT_TIMEOUT = 5.0


def _session_path(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("path is not a string")
    path = posixpath.normpath(value)
    if not path.startswith(SESSION_ROOT):
        raise ValueError(f"path outside the session volume: {value}")
    return path


def daemon_command(request_line: bytes) -> list[str]:
    """The command a request stands for: only its two paths come from the client."""
    request = json.loads(request_line)
    if not isinstance(request, dict):
        raise ValueError("request is not an object")
    return [
        sys.executable, "/reachable/reachable.py",
        "--archipelago", _session_path(request.get("archipelago")),
        "--yamls", _session_path(request.get("yamls")),
        "--daemon",
        "--session",
    ]


async def _relay(source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
    while data := await source.read(65536):
        target.write(data)
        await target.drain()


async def _stop(process: asyncio.subprocess.Process) -> None:
    if process.stdin is not None:
        process.stdin.close()
    try:
        await asyncio.wait_for(process.wait(), timeout=_EXIT_TIMEOUT)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


async def _serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    process = None
    try:
        command = daemon_command(await reader.readline())
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            # Daemon logs: nobody reads them, and the session logs are for the server.
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "AP_WORLDS_DIR": WORLDS_DIR},
        )
        relays = {
            asyncio.ensure_future(_relay(reader, process.stdin)),
            asyncio.ensure_future(_relay(process.stdout, writer)),
        }
        await asyncio.wait(relays, return_when=asyncio.FIRST_COMPLETED)
        for relay in relays:
            relay.cancel()
        await asyncio.gather(*relays, return_exceptions=True)
    except (ValueError, OSError) as exc:
        print(f"reachable host: {exc}", file=sys.stderr, flush=True)
    finally:
        with contextlib.suppress(OSError):
            writer.close()
        if process is not None:
            await _stop(process)


async def main() -> None:
    # A restarted container finds the previous socket file on the volume.
    with contextlib.suppress(FileNotFoundError):
        os.unlink(SOCKET_PATH)
    server = await asyncio.start_unix_server(_serve, SOCKET_PATH)
    os.chmod(SOCKET_PATH, 0o600)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
