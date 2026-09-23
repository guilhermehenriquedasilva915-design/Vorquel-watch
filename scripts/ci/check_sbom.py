"""Minimal structural validation for the generated CycloneDX SBOM."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_sbom.py SBOM", file=sys.stderr)
        return 2
    payload = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    components = payload.get("components")
    if payload.get("bomFormat") != "CycloneDX" or not isinstance(components, list):
        print("invalid CycloneDX SBOM", file=sys.stderr)
        return 1
    print(payload["bomFormat"], payload.get("specVersion"), len(components), "components")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
