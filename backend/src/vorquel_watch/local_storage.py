from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path


class IntegrityError(RuntimeError):
    """A stored object does not match the digest it is filed under."""


class LocalStorage:
    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir.resolve()
        self.objects = self.root / "objects" / "sha256"
        self.exports = self.root / "exports"
        self.jobs = self.root / "jobs"
        self.models = self.root / "models"
        self.cache = self.root / "cache"
        self.quarantine = self.root / "quarantine"
        self.logs = self.root / "logs"

    def ensure(self) -> None:
        for path in (
            self.objects,
            self.exports,
            self.jobs,
            self.models,
            self.cache,
            self.quarantine,
            self.logs,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def object_path(self, content_sha256: str) -> Path:
        if (
            len(content_sha256) != 64
            or any(c not in "0123456789abcdef" for c in content_sha256)
        ):
            raise ValueError("invalid sha256")
        return (
            self.objects
            / content_sha256[:2]
            / content_sha256[2:4]
            / content_sha256
            / "source.media"
        )

    def record_integrity_incident(self, event: dict[str, object]) -> None:
        """Append a local incident record.

        Only digests and relative names are recorded: no host path, no media
        content.
        """
        self.logs.mkdir(parents=True, exist_ok=True)
        entry = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            **event,
        }
        with (self.logs / "integrity-incidents.jsonl").open(
            "a", encoding="utf-8"
        ) as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def quarantine_object(self, content_sha256: str, actual_sha256: str) -> Path:
        """Move a corrupted object aside. Evidence is never silently overwritten."""
        self.quarantine.mkdir(parents=True, exist_ok=True)
        source = self.object_path(content_sha256)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = self.quarantine / f"{content_sha256}.{stamp}.media"

        shutil.move(str(source), str(target))
        self.record_integrity_incident(
            {
                "event": "cache_quarantine",
                "expected_sha256": content_sha256,
                "actual_sha256": actual_sha256,
                "quarantined_as": target.name,
            }
        )
        return target

    def verify_object(self, content_sha256: str) -> Path:
        """Re-hash a stored object and return its path, or raise.

        SEC-04. Called before an object is processed, so a file that changed on
        disk after ingest cannot be transcribed as though it were the original
        evidence.
        """
        target = self.object_path(content_sha256)
        if not target.is_file():
            raise IntegrityError("local media object is missing")

        actual = sha256_file(target)
        if actual != content_sha256:
            self.quarantine_object(content_sha256, actual)
            raise IntegrityError("local media object failed integrity check")
        return target

    def store_source(self, source: Path, content_sha256: str) -> Path:
        self.ensure()
        target = self.object_path(content_sha256)
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.exists():
            # SEC-04: never trust a content-addressed hit on its name alone.
            # The previous code returned here without reading the file, so a
            # corrupted or swapped object would be processed as genuine
            # evidence under a digest it no longer matches.
            actual = sha256_file(target)
            if actual == content_sha256:
                return target
            self.quarantine_object(content_sha256, actual)

        with tempfile.NamedTemporaryFile(
            dir=target.parent,
            prefix="incoming-",
            delete=False,
        ) as handle:
            temp = Path(handle.name)

        try:
            shutil.copyfile(source, temp)
            copied_hash = sha256_file(temp)
            if copied_hash != content_sha256:
                raise IntegrityError("source changed during ingest")
            temp.replace(target)
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)
        return target

    def export_path(self, artifact_id: str, suffix: str) -> Path:
        if "/" in artifact_id or "\\" in artifact_id:
            raise ValueError("invalid artifact id")
        safe_suffix = suffix if suffix.startswith(".") else f".{suffix}"
        target = (self.exports / f"{artifact_id}{safe_suffix}").resolve()
        if self.exports.resolve() not in target.parents:
            raise ValueError("export escaped storage root")
        self.exports.mkdir(parents=True, exist_ok=True)
        return target


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()
