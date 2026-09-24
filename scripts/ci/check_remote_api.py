"""Remote API deployment invariants (ADR 0009).

These are the properties that make it safe to run the API on a VPS at all, and
they are the ones most likely to be broken by a well-meaning change:

1. it refuses to start without a token, and refuses a guessable one;
2. it defaults to loopback, so nothing is exposed by forgetting to configure it;
3. it adds nothing to the MCP surface.

The unit suite covers the behaviour in detail. This gate exists because these
three are deployment properties: if any of them regresses, the mistake ships to a
machine on the internet rather than failing on a laptop.
"""

from __future__ import annotations

import os
import sys


def _fail(message: str) -> None:
    print(f"remote api check failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def check_refuses_without_token() -> None:
    from vorquel_watch import remote_auth

    saved = {
        key: os.environ.pop(key, None)
        for key in (remote_auth.TOKEN_ENV_VAR, remote_auth.TOKEN_FILE_ENV_VAR)
    }
    try:
        try:
            remote_auth.load_server_token()
        except remote_auth.TokenNotConfigured:
            pass
        else:
            _fail("the API accepted a missing token")

        os.environ[remote_auth.TOKEN_ENV_VAR] = "short"
        try:
            remote_auth.load_server_token()
        except remote_auth.TokenNotConfigured:
            pass
        else:
            _fail("the API accepted a token below the minimum length")
    finally:
        os.environ.pop(remote_auth.TOKEN_ENV_VAR, None)
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value


def check_loopback_default() -> None:
    """A forgotten VORQUEL_WATCH_REMOTE_HOST must not mean 0.0.0.0."""
    import inspect

    from vorquel_watch import remote_api, remote_client

    source = inspect.getsource(remote_api.main)
    if '"127.0.0.1"' not in source:
        _fail("remote_api.main no longer defaults to a loopback bind")
    if "0.0.0.0" in source:
        _fail("remote_api.main mentions a wildcard bind address")

    if not remote_client.DEFAULT_BASE_URL.startswith("http://127.0.0.1"):
        _fail("the client default base URL is not loopback")


def check_no_public_route_without_auth() -> None:
    from vorquel_watch import remote_api

    allowed = {"/v1/health"}
    if set(remote_api._PUBLIC_PATHS) != allowed:
        _fail(
            "the set of unauthenticated routes changed: "
            f"{sorted(remote_api._PUBLIC_PATHS)}"
        )


def check_mcp_surface_unchanged() -> None:
    import asyncio

    from mcp import Client

    from vorquel_watch.mcp_server import mcp

    async def run() -> set[str]:
        async with Client(mcp) as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    names = asyncio.run(run())
    if len(names) != 24:
        _fail(f"the MCP surface is no longer 24 tools: {len(names)}")
    leaked = sorted(n for n in names if "remote" in n or "upload" in n)
    if leaked:
        _fail(f"remote processing leaked onto the MCP surface: {leaked}")


def main() -> int:
    check_refuses_without_token()
    check_loopback_default()
    check_no_public_route_without_auth()
    check_mcp_surface_unchanged()
    print("remote api deployment invariants hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
