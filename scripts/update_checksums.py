"""Recompute frozen-file checksums exactly as verify_skill_checksums() checks them.

Hashes are SHA256 of the file with CRLF normalized to LF. Only paths already listed
in releases/checksums.sha256 are updated (the frozen set does not grow silently).

    python scripts/update_checksums.py            # rewrite releases/checksums.sha256
    python scripts/update_checksums.py --check    # exit 1 if any entry is stale
    python scripts/update_checksums.py --also <other>/releases/checksums.sha256 ...

Re-running scripts/install.py afterwards copies releases/ into the Workspace pack and
its runtime/ copy, which keeps all three locations identical.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def recompute(root: Path, checksum_file: Path) -> tuple[list[str], list[str]]:
    lines, stale = [], []
    for raw in checksum_file.read_text(encoding="utf-8").splitlines():
        parts = raw.strip().split(None, 1)
        if len(parts) != 2 or raw.strip().startswith("#"):
            lines.append(raw)
            continue
        expected, rel = parts[0], parts[1].strip()
        target = root / rel
        if not target.is_file():
            raise SystemExit(f"listed file missing: {rel}")
        actual = file_hash(target)
        if actual != expected.lower():
            stale.append(rel)
        lines.append(f"{actual}  {rel}")
    return lines, stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--also", nargs="*", default=[], help="extra checksum files to overwrite with the same content")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    main_file = root / "releases" / "checksums.sha256"
    lines, stale = recompute(root, main_file)
    if args.check:
        for rel in stale:
            print(f"STALE {rel}")
        print("ALL CHECKSUMS MATCH" if not stale else f"{len(stale)} stale entries")
        return 1 if stale else 0
    content = "\n".join(lines) + "\n"
    for target in [main_file, *map(Path, args.also)]:
        target.write_text(content, encoding="utf-8", newline="\n")
        print(f"wrote {target}")
    for rel in stale:
        print(f"updated {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
