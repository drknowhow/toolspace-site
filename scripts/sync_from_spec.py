"""
Sync schema + examples from the install-manifest-spec repo into this site.

Usage:
  python scripts/sync_from_spec.py [--check] [--spec-path PATH]

  --check       Verify byte-identity without writing. Exit 1 on mismatch.
  --spec-path   Path to a local clone of install-manifest-spec.
                Defaults to ../install-manifest-spec relative to this site.

In CI we run with --check to fail builds on drift. Locally, drop the flag
to overwrite this site's copies with the spec's canonical files.

Pattern banked from install-manifest-spec PR #1 dogfood (2026-05-05): when
a package needs a data file that also exists in another repo, mirror the
file + add a byte-identity test. Cheap insurance against silent divergence.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SITE_ROOT = Path(__file__).resolve().parent.parent

# (relative path under spec, relative path under site)
FILES_TO_SYNC: list[tuple[str, str]] = [
    ("schema/install-manifest-v0.1.json", "schemas/install-manifest-v0.1.json"),
    ("schema/install-manifest-v0.2.json", "schemas/install-manifest-v0.2.json"),
    ("examples/gmail.json", "examples/gmail.json"),
    ("examples/gmail.v0.2.json", "examples/gmail.v0.2.json"),
]


def _resolve_spec_path(arg: str | None) -> Path:
    if arg:
        p = Path(arg).resolve()
    else:
        p = (SITE_ROOT.parent / "install-manifest-spec").resolve()
    if not p.is_dir():
        raise SystemExit(
            f"install-manifest-spec not found at {p}. "
            "Pass --spec-path or clone the repo as a sibling of this one."
        )
    return p


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Verify byte-identity, do not write."
    )
    parser.add_argument(
        "--spec-path", default=None, help="Path to install-manifest-spec clone."
    )
    args = parser.parse_args()

    spec_root = _resolve_spec_path(args.spec_path)

    drift = []
    for spec_rel, site_rel in FILES_TO_SYNC:
        src = spec_root / spec_rel
        dst = SITE_ROOT / site_rel
        if not src.is_file():
            raise SystemExit(f"missing source: {src}")
        src_bytes = src.read_bytes()
        dst_bytes = dst.read_bytes() if dst.is_file() else b""
        if src_bytes == dst_bytes:
            print(f"  ok   {site_rel}")
            continue
        if args.check:
            drift.append(site_rel)
            print(f"  DRIFT {site_rel}", file=sys.stderr)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src_bytes)
            print(f"  sync {site_rel}")

    if args.check and drift:
        print(
            f"\nERROR: {len(drift)} file(s) drift from install-manifest-spec.\n"
            "Run 'python scripts/sync_from_spec.py' locally and commit the result.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
