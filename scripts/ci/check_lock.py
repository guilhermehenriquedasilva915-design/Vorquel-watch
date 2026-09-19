"""Compare two pip-compile locks while ignoring generated comments."""

from __future__ import annotations

import difflib
import sys
from pathlib import Path


def normalized(path: Path) -> list[str]:
    return [
        line.rstrip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: check_lock.py CURRENT EXPECTED", file=sys.stderr)
        return 2

    current = normalized(Path(argv[1]))
    expected = normalized(Path(argv[2]))
    if current == expected:
        print(f"lock in sync: {len(current)} non-comment lines")
        return 0

    print("backend/requirements.lock is out of date.", file=sys.stderr)
    for line in difflib.unified_diff(
        current,
        expected,
        fromfile=argv[1],
        tofile=argv[2],
        lineterm="",
    ):
        print(line, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
