"""Cross-platform unittest gate used by GitHub Actions."""

from __future__ import annotations

import sys
import unittest


def main() -> int:
    suite = unittest.defaultTestLoader.discover("backend/tests", pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)

    blocked = [
        (test, reason)
        for test, reason in result.skipped
        if "media runtime" in reason
    ]
    for test, reason in blocked:
        print(f"SKIPPED {test}: {reason}", file=sys.stderr)

    if blocked:
        print("media-dependent tests must not skip in CI", file=sys.stderr)
        return 2
    if not result.wasSuccessful():
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
