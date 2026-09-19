# Dependency management and supply chain

## Why there is a lock file

`backend/pyproject.toml` declares direct dependencies with exact `==` pins, but
that says nothing about their transitive dependencies. Without a lock, two
installs a week apart can resolve to different code, and an audit performed on
one of them says nothing about the other.

`backend/requirements.lock` pins the full resolved set — 63 packages — with
SHA-256 hashes for every artifact, so an install either reproduces the reviewed
set exactly or fails.

## Regenerating the lock

Run from the repository root so the recorded command stays portable; a lock
generated with absolute paths leaks the host layout and is not reproducible
elsewhere.

```
pip install "pip-tools==7.6.1"
pip-compile --generate-hashes --strip-extras --extra transcribe --extra screen \
  --output-file backend/requirements.lock backend/pyproject.toml
```

Regenerate whenever `pyproject.toml` changes. CI fails if the lock has drifted.

## Installing from the lock

```
pip install --require-hashes -r backend/requirements.lock
pip install -e backend --no-deps
```

`--require-hashes` makes pip refuse any artifact whose hash does not match.
`--no-deps` on the second command stops pip re-resolving what the lock just
fixed.

## What CI enforces

The `dependencies` job runs on every pull request:

| Step | Purpose |
|---|---|
| Lock is in sync with pyproject | Regenerates and diffs, ignoring comments. A drifted lock fails the build. |
| Vulnerability audit | `pip-audit` against the locked set. |
| SBOM | CycloneDX 1.4 inventory of the locked set. |

`pip check` still runs in the main job, but it is a **compatibility** check, not
a vulnerability scanner, and is not treated as one.

Both `pip-tools` and `pip-audit` are pinned in CI. An unpinned security tool is
its own supply-chain gap.

## Current state

Measured on 2026-09-18 against `backend/requirements.lock`:

- exact package/artifact counts are produced by the current CI lock/audit run;
- `pip-audit` is run against the hash-locked V1 runtime;
- the SBOM is generated as CycloneDX JSON on every pull request.

## Media stack

`av` (PyAV) 18.1.0 ships FFmpeg 8.1.2 in its binary wheels, confirmed at runtime
through `av.library_versions` (`libavcodec 62.28.102`). FFmpeg 8.1.2 is the
release that fixes CVE-2026-8461 ("PixelSmash"), a heap out-of-bounds write in
the MagicYUV decoder reachable through the AVI/MKV/MOV demuxers.

That CVE is therefore fixed in the shipped build. This is not the same as the
decoder surface being small: the bundled build enables 557 codecs, MagicYUV
among them. Source Guard restricts what is accepted rather than relying on the
decoder surface being safe — see the container allowlist in
`backend/src/vorquel_watch/source_guard.py`, which excludes AVI.

FFmpeg 9.0.2 is the current upstream stable release; PyAV has not moved to it.

## Adding a dependency

Before adding anything, check maintenance status, license, known
vulnerabilities, and whether it is actually needed. Prefer the standard library:
the DPAPI credential store in `credentials.py` uses `ctypes` precisely to avoid
taking a dependency for it.

If third-party code is vendored rather than depended on, record its license and
attribution in `THIRD_PARTY_NOTICES.md` before merging.
