"""Cross-platform unit-test gate.

Each test module runs in a fresh Python process. The previous in-process
unittest discovery suite exhibited cross-module state leakage on the Windows
runner even though every module passed independently. Isolation keeps the gate
strict while preventing one test module's process globals from contaminating
another. Any non-zero module exit still fails CI.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "backend" / "tests"


def main() -> int:
    files = sorted(TESTS.glob("test_*.py"))
    if not files:
        print("no test modules found", file=sys.stderr)
        return 2

    failures: list[Path] = []
    for path in files:
        print(f"=== {path.relative_to(ROOT)} ===", flush=True)
        proc = subprocess.run(
            [sys.executable, str(path)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.stdout:
            print(proc.stdout, end="")
        if proc.stderr:
            print(proc.stderr, end="", file=sys.stderr)
        if proc.returncode != 0:
            failures.append(path)

    if failures:
        print(
            "failed test modules: "
            + ", ".join(str(p.relative_to(ROOT)) for p in failures),
            file=sys.stderr,
        )
        return 1

    print(f"all {len(files)} test modules passed in isolated processes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
