"""Operating-system backed secret storage (SEC-03).

The Supabase server credential must not sit in a plaintext file. On Windows the
previous approach wrote it to config.env and called chmod, which is a POSIX
no-op there: the file stayed readable by anything running as the user.

This module encrypts the secret with DPAPI (CryptProtectData), which derives
its key from the logged-in Windows account. The ciphertext is useless to
another user account and useless on another machine. DPAPI is reached through
ctypes, so this adds no third-party dependency and no new supply-chain risk.

It fails closed: on a platform without DPAPI it refuses rather than silently
falling back to plaintext.

Nothing here logs, prints or formats a secret value.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path


CREDENTIAL_DIR_NAME = "credentials"
SUPABASE_SECRET_NAME = "supabase_secret_key"

# Never show an interactive DPAPI prompt from a background worker or MCP server.
_CRYPTPROTECT_UI_FORBIDDEN = 0x1

# Ties the ciphertext to this application, so a blob lifted from another DPAPI
# consumer running as the same user cannot be decrypted here, and vice versa.
_ENTROPY = b"vorquel-watch/credential/v1"


class CredentialError(RuntimeError):
    """Raised when a secret cannot be stored or retrieved.

    The message never contains the secret itself.
    """


def is_supported() -> bool:
    return sys.platform == "win32"


class _Blob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]

    @classmethod
    def from_bytes(cls, raw: bytes) -> "_Blob":
        buffer = ctypes.create_string_buffer(raw, len(raw))
        return cls(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))

    def value(self) -> bytes:
        return ctypes.string_at(self.pbData, self.cbData)


def _require_windows() -> None:
    if not is_supported():
        raise CredentialError(
            "OS-backed credential storage requires Windows (DPAPI). "
            "Refusing to store the credential in plaintext."
        )


def _crypt(func_name: str, payload: bytes) -> bytes:
    _require_windows()

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    func = getattr(crypt32, func_name)
    data_in = _Blob.from_bytes(payload)
    entropy = _Blob.from_bytes(_ENTROPY)
    data_out = _Blob()

    # CryptProtectData and CryptUnprotectData take the same argument list, so
    # one call covers both directions.
    ok = func(
        ctypes.byref(data_in),
        None,
        ctypes.byref(entropy),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(data_out),
    )

    if not ok:
        # Report only the Windows error code: the payload must never surface.
        raise CredentialError(
            f"{func_name} failed (Windows error {ctypes.get_last_error()})"
        )

    try:
        return data_out.value()
    finally:
        kernel32.LocalFree(data_out.pbData)


def _credential_path(data_dir: Path, name: str) -> Path:
    if not name.isidentifier():
        raise CredentialError("invalid credential name")
    directory = (data_dir / CREDENTIAL_DIR_NAME).resolve()
    target = (directory / f"{name}.bin").resolve()
    if directory != target.parent:
        raise CredentialError("credential path escaped the credential directory")
    return target


def store_secret(data_dir: Path, name: str, secret: str) -> Path:
    """Encrypt and persist a secret. Returns the ciphertext path."""
    _require_windows()
    if not secret:
        raise CredentialError("refusing to store an empty secret")

    target = _credential_path(data_dir, name)
    target.parent.mkdir(parents=True, exist_ok=True)

    ciphertext = _crypt("CryptProtectData", secret.encode("utf-8"))

    # Write via a temporary file in the same directory, then replace, so a
    # crash cannot leave a half-written credential behind.
    temp = target.with_suffix(".tmp")
    temp.write_bytes(ciphertext)
    temp.replace(target)
    return target


def load_secret(data_dir: Path, name: str) -> str | None:
    """Return the decrypted secret, or None if it was never stored."""
    _require_windows()
    target = _credential_path(data_dir, name)
    if not target.is_file():
        return None
    return _crypt("CryptUnprotectData", target.read_bytes()).decode("utf-8")


def delete_secret(data_dir: Path, name: str) -> bool:
    """Remove a stored secret. Returns whether one was present."""
    target = _credential_path(data_dir, name)
    if not target.is_file():
        return False
    target.unlink()
    return True


def has_secret(data_dir: Path, name: str) -> bool:
    return _credential_path(data_dir, name).is_file()
