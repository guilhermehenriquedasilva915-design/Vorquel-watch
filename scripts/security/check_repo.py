from __future__ import annotations

import pathlib
import re
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]

FORBIDDEN_TRACKED_NAMES = {".env", "id_rsa", "id_ed25519"}
FORBIDDEN_MEDIA_SUFFIXES = {
    ".mp4", ".mkv", ".mov", ".avi", ".webm",
    ".mp3", ".wav", ".m4a", ".flac",
}
FORBIDDEN_SOURCE_PATTERNS = {
    "shell=True": re.compile(r"shell\s*=\s*True"),
    "os.system": re.compile(r"\bos\.system\s*\("),
}
SHA_PIN = re.compile(r"^[0-9a-f]{40}$")


def tracked_files() -> list[pathlib.Path]:
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [ROOT / line for line in proc.stdout.splitlines() if line]


def check_tracked_files(files: list[pathlib.Path]) -> list[str]:
    errors: list[str] = []
    for path in files:
        rel = path.relative_to(ROOT)
        if path.name in FORBIDDEN_TRACKED_NAMES:
            errors.append(f"forbidden tracked secret filename: {rel}")
        if path.suffix.lower() in FORBIDDEN_MEDIA_SUFFIXES:
            errors.append(f"raw media must not be tracked: {rel}")
        if path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}:
            errors.append(f"credential/key material must not be tracked: {rel}")
    return errors


def check_python_source() -> list[str]:
    errors: list[str] = []
    src = ROOT / "backend" / "src"
    if not src.exists():
        return errors
    for path in src.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for label, pattern in FORBIDDEN_SOURCE_PATTERNS.items():
            if pattern.search(text):
                errors.append(
                    f"forbidden Python source pattern {label!r}: "
                    f"{path.relative_to(ROOT)}"
                )
    return errors


def check_action_pins() -> list[str]:
    errors: list[str] = []
    workflows = ROOT / ".github" / "workflows"
    if not workflows.exists():
        return errors
    for path in [*workflows.glob("*.yml"), *workflows.glob("*.yaml")]:
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            stripped = line.strip()
            if not stripped.startswith("- uses:") and not stripped.startswith("uses:"):
                continue
            ref = stripped.split("uses:", 1)[1].strip().split("#", 1)[0].strip()
            if "@" not in ref:
                errors.append(f"unversioned action at {path}:{number}")
                continue
            _, version = ref.rsplit("@", 1)
            if not SHA_PIN.fullmatch(version):
                errors.append(
                    f"GitHub Action is not pinned to a 40-char SHA at "
                    f"{path.relative_to(ROOT)}:{number}: {ref}"
                )
    return errors


def main() -> int:
    errors = []
    files = tracked_files()
    errors.extend(check_tracked_files(files))
    errors.extend(check_python_source())
    errors.extend(check_action_pins())

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print("Repository security bootstrap checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
