from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import tempfile


class LocalStorage:
    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir.resolve()
        self.objects = self.root / "objects" / "sha256"
        self.exports = self.root / "exports"
        self.jobs = self.root / "jobs"
        self.models = self.root / "models"

    def ensure(self) -> None:
        for path in (self.objects, self.exports, self.jobs, self.models):
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

    def store_source(self, source: Path, content_sha256: str) -> Path:
        self.ensure()
        target = self.object_path(content_sha256)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            return target

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
                raise RuntimeError("source changed during ingest")
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
