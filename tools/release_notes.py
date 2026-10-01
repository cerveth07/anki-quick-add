"""Extract a documented release from CHANGELOG.md and validate its version."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def release_notes(tag: str) -> str:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) or tag != f"v{version}":
        raise ValueError("Release tag must match VERSION (vX.Y.Z)")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = re.search(rf"^## \[{re.escape(version)}\] - [0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}\s*$", changelog, re.MULTILINE)
    if heading is None:
        raise ValueError("CHANGELOG.md is missing the version/date entry")
    body = changelog[heading.end():]
    body = re.split(r"^## ", body, maxsplit=1, flags=re.MULTILINE)[0].strip()
    if not body:
        raise ValueError("Release changelog entry must contain update notes")
    return body + "\n"


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: release_notes.py vX.Y.Z output.md")
    try:
        notes = release_notes(sys.argv[1])
    except ValueError as error:
        raise SystemExit(str(error)) from error
    Path(sys.argv[2]).write_text(notes, encoding="utf-8")
