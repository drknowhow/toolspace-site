"""
Discover and validate federated publishers' well-known indexes.

Reads publishers.json (the maintainer-curated allowlist), fetches each
publisher's .well-known/install-manifests.json at its kind-specific
discovery URL, and validates the response against the well-known-index
v1 contract.

Usage:
  python scripts/discover_publishers.py [--check] [--json]

  --check   Exit 1 if any publisher's index fails fetch or validation.
            Intended for CI.
  --json    Emit a machine-readable report to stdout.

This script is read-only — it does not mutate manifests.json. The
sync_from_publishers.py script handles the merge.

stdlib-only. No third-party dependencies.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
from pathlib import Path

# Sibling-module import (federation.py lives next to this file).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from federation import (
    PUBLISHER_KINDS,
    discovery_url_for,
    fetch_url,
    validate_well_known_index,
)

SITE_ROOT = Path(__file__).resolve().parent.parent
PUBLISHERS_PATH = SITE_ROOT / "publishers.json"


def _load_publishers() -> list[dict]:
    if not PUBLISHERS_PATH.is_file():
        raise SystemExit(f"missing: {PUBLISHERS_PATH}")
    data = json.loads(PUBLISHERS_PATH.read_text(encoding="utf-8"))
    if data.get("version") != "1":
        raise SystemExit(f"publishers.json: unsupported version {data.get('version')!r}")
    pubs = data.get("publishers")
    if not isinstance(pubs, list):
        raise SystemExit("publishers.json: publishers must be an array")
    return pubs


def _discover_one(publisher: dict) -> dict:
    """Fetch + validate one publisher. Returns a result dict, never raises."""
    pid = publisher.get("id", "?")
    kind = publisher.get("kind", "?")
    result = {
        "id": pid,
        "kind": kind,
        "display_name": publisher.get("display_name"),
        "url": None,
        "ok": False,
        "error": None,
        "manifest_count": 0,
        "manifest_ids": [],
    }

    if kind not in PUBLISHER_KINDS:
        result["error"] = f"unknown kind: {kind!r}"
        return result

    if kind == "atproto":
        # v1 registries don't yet resolve at-uris. Surface as a known gap.
        result["error"] = "atproto kind not yet resolvable; pending AT lexicon publication"
        return result

    try:
        url = discovery_url_for(publisher)
    except ValueError as e:
        result["error"] = str(e)
        return result
    result["url"] = url

    try:
        raw = fetch_url(url)
    except urllib.error.HTTPError as e:
        result["error"] = f"HTTP {e.code} from {url}"
        return result
    except urllib.error.URLError as e:
        result["error"] = f"URL error fetching {url}: {e.reason}"
        return result
    except (TimeoutError, ValueError, OSError) as e:
        result["error"] = f"fetch error: {e}"
        return result

    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        result["error"] = f"not valid UTF-8 JSON: {e}"
        return result

    errors = validate_well_known_index(doc)
    if errors:
        result["error"] = f"validation failed: {errors[0]}" + (
            f" (+{len(errors)-1} more)" if len(errors) > 1 else ""
        )
        result["validation_errors"] = errors
        return result

    # Self-consistency: publisher.id in the document must match the
    # publisher.id from the allowlist.
    doc_pub = doc.get("publisher", {})
    if doc_pub.get("kind") != kind or doc_pub.get("id") != pid:
        result["error"] = (
            f"publisher identity mismatch: allowlist says ({kind!r}, {pid!r}), "
            f"document says ({doc_pub.get('kind')!r}, {doc_pub.get('id')!r})"
        )
        return result

    manifests = doc.get("manifests", [])
    result["manifest_count"] = len(manifests)
    result["manifest_ids"] = [m.get("id") for m in manifests]
    result["ok"] = True
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if any publisher fails fetch or validation.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    publishers = _load_publishers()
    results = [_discover_one(p) for p in publishers]

    if args.json:
        print(json.dumps({"results": results}, indent=2))
    else:
        for r in results:
            tag = "OK  " if r["ok"] else "FAIL"
            label = f"{r['kind']}:{r['id']}"
            extra = (
                f"{r['manifest_count']} manifests" if r["ok"] else (r["error"] or "?")
            )
            print(f"  [{tag}] {label} — {extra}")
        ok_count = sum(1 for r in results if r["ok"])
        print(
            f"\n{ok_count}/{len(results)} publishers reachable; "
            f"{sum(r['manifest_count'] for r in results)} manifests indexed total"
        )

    if args.check and any(not r["ok"] for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
