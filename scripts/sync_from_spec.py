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
import re
import sys
from pathlib import Path

SITE_ROOT = Path(__file__).resolve().parent.parent

# (relative path under spec, relative path under site)
FILES_TO_SYNC: list[tuple[str, str]] = [
    ("schema/install-manifest-v0.1.json", "schemas/install-manifest-v0.1.json"),
    ("schema/install-manifest-v0.2.json", "schemas/install-manifest-v0.2.json"),
    ("schema/install-manifest-v0.3.json", "schemas/install-manifest-v0.3.json"),
    ("examples/gmail.json", "examples/gmail.json"),
    ("examples/gmail.v0.2.json", "examples/gmail.v0.2.json"),
    ("examples/gmail.v0.3.json", "examples/gmail.v0.3.json"),
    ("examples/muninn-verify-patch.v0.3.json", "examples/muninn-verify-patch.v0.3.json"),
    ("examples/muninn-flowing.v0.3.json", "examples/muninn-flowing.v0.3.json"),
    ("examples/muninn-perch-publish.v0.3.json", "examples/muninn-perch-publish.v0.3.json"),
]

# Pattern matching the spec README's version table row marked "current".
# Example match: `| **0.4** | current | [...](...) | ...`
_CURRENT_VERSION_ROW = re.compile(
    r"^\|\s*\*\*(?P<v>[0-9]+\.[0-9]+(?:\.[0-9]+)?)\*\*\s*\|\s*current\s*\|",
    re.MULTILINE,
)


def _read_spec_current_version(spec_root: Path) -> str:
    """Parse spec README for the row tagged 'current' in the version table.

    The spec README's version table is the single source of truth for which
    schema version this site should advertise on the homepage. Bumping the
    spec's `current` row is the trigger; this site must follow.
    """
    readme = spec_root / "README.md"
    if not readme.is_file():
        raise SystemExit(f"missing spec README: {readme}")
    matches = _CURRENT_VERSION_ROW.findall(readme.read_text(encoding="utf-8"))
    if not matches:
        raise SystemExit(
            f"could not find a 'current' row in {readme}. "
            "Expected a version table line like `| **0.4** | current | ...`."
        )
    if len(matches) > 1:
        raise SystemExit(
            f"found {len(matches)} 'current' rows in {readme}: {matches}. "
            "The version table must have exactly one current version."
        )
    return matches[0]


def _check_homepage_advertises_current(current: str) -> list[str]:
    """Verify index.html's 'What a manifest looks like' snippet matches `current`.

    Two pinned references on the landing page must track the spec's current
    version, or readers see contradictory info next to the For-agents pills:
      1. The example JSON snippet's `"manifest_version": "<X.Y>"`.
      2. The "Every field is documented in the v<X.Y> schema" link, which
         must resolve to `/schemas/install-manifest-v<X.Y>.json`.

    Returns a list of human-readable drift messages (empty = OK).
    """
    index_path = SITE_ROOT / "index.html"
    if not index_path.is_file():
        raise SystemExit(f"missing site index: {index_path}")
    html = index_path.read_text(encoding="utf-8")

    drift: list[str] = []

    snippet_needle = f'"manifest_version": "{current}"'
    if snippet_needle not in html:
        # Find what version it currently advertises, for a useful message.
        m = re.search(r'"manifest_version":\s*"([^"]+)"', html)
        found = m.group(1) if m else "(none)"
        drift.append(
            f'index.html snippet advertises manifest_version="{found}" '
            f'but spec README marks {current} as current. '
            f'Update the "What a manifest looks like" snippet.'
        )

    schema_needle = f"/schemas/install-manifest-v{current}.json"
    if schema_needle not in html:
        drift.append(
            f"index.html does not link to {schema_needle}. "
            f"The 'Every field is documented in the v{current} schema' link "
            f"must reference the current schema URL."
        )

    return drift


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

    # Homepage version-drift guard: spec README's "current" row is SSOT for the
    # version advertised in the "What a manifest looks like" snippet on
    # index.html. Whenever the spec bumps `current`, this guard fires until the
    # homepage snippet + schema link are updated to match.
    current = _read_spec_current_version(spec_root)
    print(f"  spec current: v{current}")
    homepage_drift = _check_homepage_advertises_current(current)
    for msg in homepage_drift:
        print(f"  DRIFT {msg}", file=sys.stderr)
    if not homepage_drift:
        print(f"  ok   index.html advertises v{current}")

    if args.check and (drift or homepage_drift):
        n_files = len(drift)
        n_homepage = len(homepage_drift)
        print(
            f"\nERROR: {n_files} file(s) drift from install-manifest-spec; "
            f"{n_homepage} homepage version-drift issue(s).\n"
            "For file drift: run 'python scripts/sync_from_spec.py' locally and commit.\n"
            "For homepage drift: edit index.html's 'What a manifest looks like' "
            "snippet and the linked schema URL to match the spec's current version.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
