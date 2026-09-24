"""Bearer-token authentication for the remote processing API (SEC-08).

The remote API is the only network surface the Watch exposes. It sits in front
of ingest, so an unauthenticated caller reaching it could spend the VPS's disk
and CPU at will. Authentication therefore happens before a single byte of the
request body is read.

The token is not the Supabase credential and carries none of its authority. It
authorizes "submit media for processing and read job state on this host", and
nothing else. A client holding it still cannot read a host path, run a command
or reach the database directly, because no route offers those.

Nothing here logs, prints or returns a token value. Failures say only that
authentication failed: a caller learns whether it is authorized, never how
close it got.
"""

from __future__ import annotations

import hmac
import os
from pathlib import Path


TOKEN_ENV_VAR = "VORQUEL_WATCH_REMOTE_TOKEN"
TOKEN_FILE_ENV_VAR = "VORQUEL_WATCH_REMOTE_TOKEN_FILE"
CLIENT_TOKEN_SECRET_NAME = "remote_api_token"

# A guessable token is the same as no token. 32 characters is the floor for a
# value that is plausibly random rather than a passphrase someone typed.
MIN_TOKEN_LENGTH = 32

_SCHEME = "bearer"


class TokenNotConfigured(RuntimeError):
    """The server has no usable token, so it must not start.

    Fails closed on purpose. An API that starts without a token would accept
    every caller, which is worse than not starting at all.
    """


def _clean(value: str | None) -> str:
    return (value or "").strip()


def load_server_token() -> str:
    """Return the token this server requires, or refuse to start.

    Read from the environment, or from a file named by the environment so a
    systemd unit can keep the value in a root-owned 0600 file rather than in a
    unit file that ends up in git. The file's contents are stripped, so a
    trailing newline from `printf`/`echo` is not part of the secret.
    """
    direct = _clean(os.environ.get(TOKEN_ENV_VAR))
    if direct:
        return _validated(direct)

    token_file = _clean(os.environ.get(TOKEN_FILE_ENV_VAR))
    if token_file:
        path = Path(token_file).expanduser()
        try:
            contents = path.read_text(encoding="utf-8")
        except OSError:
            # The path is deliberately not echoed: it is operator-supplied and
            # belongs in the operator's own configuration, not in an error.
            raise TokenNotConfigured(
                f"{TOKEN_FILE_ENV_VAR} is set but the token file could not be read."
            ) from None
        return _validated(_clean(contents))

    raise TokenNotConfigured(
        f"The remote API requires {TOKEN_ENV_VAR} or {TOKEN_FILE_ENV_VAR}. "
        "Refusing to start without authentication."
    )


def _validated(token: str) -> str:
    if len(token) < MIN_TOKEN_LENGTH:
        raise TokenNotConfigured(
            f"The remote API token must be at least {MIN_TOKEN_LENGTH} "
            "characters. Refusing to start with a guessable token."
        )
    return token


def token_from_header(header_value: str | None) -> str:
    """Extract the credential from an Authorization header.

    Returns an empty string for anything malformed rather than raising, so a
    hostile header shape is an ordinary authentication failure and not a
    different, distinguishable outcome.
    """
    raw = _clean(header_value)
    if not raw:
        return ""
    parts = raw.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != _SCHEME:
        return ""
    return parts[1].strip()


def is_authorized(header_value: str | None, expected_token: str) -> bool:
    """Constant-time comparison of a presented credential against the expected one.

    `hmac.compare_digest` keeps the comparison independent of how many leading
    characters matched, so response timing does not leak the token one character
    at a time.
    """
    presented = token_from_header(header_value)
    if not presented or not expected_token:
        return False
    return hmac.compare_digest(
        presented.encode("utf-8"), expected_token.encode("utf-8")
    )
