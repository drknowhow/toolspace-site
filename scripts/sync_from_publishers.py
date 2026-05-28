"""
Sync federated publishers into the registry's manifests.json.

Reads publishers.json, fetches each publisher's .well-known/install-
manifests.json, fetches every install-manifest URL declared therein,
and rewrites manifests.json with the merged result.

Non-federation entries (status='example' tied to no publisher, like
the original gmail.* examples) are preserved verbatim. Federation-
sourced entries are fully derived from each fetch.

Usage:
  python scripts/sync_from_publishers.py [--check] [--allow-network]

  --check          Don't write. Exit 1 if the would-be result differs
                   from the on-disk manifests.json. CI uses this to
                   detect uncommitted drift.
  --allow-network  Required for any code path that fetches remote URLs.
                   Off by default so unit tests can stub fetches via
                   the env var TOOLSPACE_FEDERATION_FIXTURES.

The federation-sync GitHub Action runs without --check to produce a
real update, then opens a PR with the diff if any.

stdlib-only. No third-party dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from federation import (
    PUBLISHER_KINDS,
    discovery_url_for,
    fetch_url,
    map_federation_status_to_registry,
    source_url_from_raw,
    validate_well_known_index,
)

SITE_ROOT = Path(__file__).resolve().parent.parent
PUBLISHERS_PATH = SITE_ROOT / "publishers.json"
MANIFESTS_PATH = SITE_ROOT / "manifests.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _fetch_with_fixtures(url: str) -> bytes:
    """Fetch a URL, optionally redirected to a local fixture directory.

    Set TOOLSPACE_FEDERATION_FIXTURES=<dir> to redirect ALL fetches at
    a fixture directory laid out as ``<dir>/<url-encoded-path>.json``
    (slashes converted to ``__``). Used by tests.
    """
    fixtures = os.environ.get("TOOLSPACE_FEDERATION_FIXTURES")
    if fixtures:
        # Filesystem-safe key: collapse "://" to "_" (single underscore between
        # scheme and host) and "/" to "__" (path-segment separator). Readable
        # filenames; no ambiguity since URLs do not contain underscores in
        # the scheme or hostname portions.
        key = url.replace("://", "_").replace("/", "__")
        path = Path(fixtures) / key
        if not path.is_file():
            raise FileNotFoundError(f"missing fixture for {url}: {path}")
        return path.read_bytes()
    return fetch_url(url)


def _fetch_publisher_index(publisher: dict) -> tuple[dict | None, str | None]:
    """Return (index_doc, error). Either side is None on the success path."""
    kind = publisher.get("kind")
    if kind not in PUBLISHER_KINDS:
        return None, f"unknown publisher kind: {kind!r}"
    if kind == "atproto":
        return None, "atproto kind not yet resolvable (pending AT lexicon)"

    try:
        url = discovery_url_for(publisher)
    except ValueError as e:
        return None, str(e)

    try:
        raw = _fetch_with_fixtures(url)
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code} from {url}"
    except urllib.error.URLError as e:
        return None, f"URL error fetching {url}: {e.reason}"
    except (TimeoutError, ValueError, OSError, FileNotFoundError) as e:
        return None, f"fetch error: {e}"

    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return None, f"not valid UTF-8 JSON: {e}"

    errors = validate_well_known_index(doc)
    if errors:
        return None, f"validation failed: {errors[0]}"

    return doc, None


def _fetch_install_manifest(url: str) -> tuple[dict | None, str | None]:
    try:
        raw = _fetch_with_fixtures(url)
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code} from {url}"
    except urllib.error.URLError as e:
        return None, f"URL error fetching {url}: {e.reason}"
    except (TimeoutError, ValueError, OSError, FileNotFoundError) as e:
        return None, f"fetch error: {e}"

    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return None, f"not valid UTF-8 JSON: {e}"
    return doc, None


def _build_registry_entry(
    index_entry: dict,
    install_manifest: dict,
    publisher: dict,
) -> dict | None:
    """Map a federation index entry + fetched install-manifest into a
    manifests.json entry. Returns None if the registry should skip
    this entry (broken / quarantined / unmappable status)."""
    status = map_federation_status_to_registry(
        index_entry.get("status", ""), publisher
    )
    if status is None:
        return None

    tool = install_manifest.get("tool", {}) or {}
    name = (
        tool.get("name")
        or index_entry.get("id", "")
    )
    description = (
        tool.get("summary")
        or index_entry.get("summary")
        or ""
    )
    capabilities = list(tool.get("tags") or index_entry.get("tags") or [])
    manifest_url = index_entry.get("manifest_url", "")
    source = source_url_from_raw(manifest_url)

    return {
        "id": index_entry.get("id"),
        "name": name,
        "description": description,
        "capabilities": capabilities,
        "manifest_url": manifest_url,
        "source": source,
        "manifest_version": index_entry.get("manifest_version"),
        "status": status,
        "federation": {
            "publisher_kind": publisher.get("kind"),
            "publisher_id": publisher.get("id"),
            "trust_tier": publisher.get("trust_tier", "standard"),
        },
    }


def _is_federated_entry(entry: dict) -> bool:
    """Heuristic: an existing manifests.json entry is federation-sourced
    if it carries a `federation` block. Pre-federation entries don't."""
    return isinstance(entry.get("federation"), dict)


def build_synced_index() -> tuple[dict, list[str]]:
    """Build the new manifests.json content + collect non-fatal warnings.

    Hard failures (allowlist absent, schema-invalid index) raise
    SystemExit. Per-publisher and per-manifest errors are collected
    as warnings so a single broken publisher doesn't block the sync.
    """
    pubs_doc = _load_json(PUBLISHERS_PATH)
    publishers = pubs_doc.get("publishers", [])
    current = _load_json(MANIFESTS_PATH)

    warnings: list[str] = []
    federated_entries: list[dict] = []
    federated_ids: set[str] = set()

    for publisher in publishers:
        label = f"{publisher.get('kind')}:{publisher.get('id')}"
        index_doc, err = _fetch_publisher_index(publisher)
        if err:
            warnings.append(f"{label}: {err}")
            continue

        for index_entry in index_doc.get("manifests", []):
            mu = index_entry.get("manifest_url", "")
            iid = index_entry.get("id")
            inst, err = _fetch_install_manifest(mu)
            if err:
                warnings.append(f"{label}/{iid}: {err}")
                continue

            # Cross-check id between index and fetched manifest.
            manifest_tool_id = (inst.get("tool") or {}).get("id")
            if manifest_tool_id and manifest_tool_id != iid:
                warnings.append(
                    f"{label}/{iid}: tool.id mismatch — fetched manifest declares "
                    f"{manifest_tool_id!r}; skipping"
                )
                continue

            registry_entry = _build_registry_entry(index_entry, inst, publisher)
            if registry_entry is None:
                continue
            if iid in federated_ids:
                warnings.append(
                    f"{label}/{iid}: duplicate id across publishers; "
                    f"first publisher wins, skipping"
                )
                continue
            federated_ids.add(iid)
            federated_entries.append(registry_entry)

    # Preserve non-federation entries (the original Yep-curated examples)
    # and drop any pre-existing federation entries — they are fully re-
    # derived from this sync run.
    preserved_entries = [
        e
        for e in current.get("manifests", [])
        if not _is_federated_entry(e) and e.get("id") not in federated_ids
    ]

    new_manifests = preserved_entries + federated_entries

    new_doc = {
        "version": current.get("version", "1"),
        "schema_url": current.get(
            "schema_url",
            "https://toolspace.yepgent.com/schemas/install-manifest-v0.4.json",
        ),
        "versions": current.get("versions", []),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "manifests": new_manifests,
    }
    return new_doc, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Don't write; exit 1 on drift.")
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="Permit remote fetches (off by default — tests must stub via fixtures).",
    )
    args = parser.parse_args()

    if not args.allow_network and not os.environ.get("TOOLSPACE_FEDERATION_FIXTURES"):
        print(
            "Refusing to run: pass --allow-network OR set "
            "TOOLSPACE_FEDERATION_FIXTURES to a fixture directory.",
            file=sys.stderr,
        )
        return 2

    new_doc, warnings = build_synced_index()

    for w in warnings:
        print(f"  WARN {w}", file=sys.stderr)

    current_raw = MANIFESTS_PATH.read_text(encoding="utf-8")
    # Generated_at varies per run; for --check we compare structurally
    # (manifests list) rather than byte-equal.
    current_doc = json.loads(current_raw)

    def _norm(d: dict) -> dict:
        d2 = dict(d)
        d2.pop("generated_at", None)
        return d2

    if _norm(current_doc) == _norm(new_doc):
        print(f"  ok: manifests.json unchanged ({len(new_doc['manifests'])} entries)")
        return 0

    if args.check:
        print(
            f"\nDRIFT: manifests.json differs from federation sync result.\n"
            f"Run 'python scripts/sync_from_publishers.py --allow-network' and commit.",
            file=sys.stderr,
        )
        return 1

    MANIFESTS_PATH.write_text(
        json.dumps(new_doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"  wrote manifests.json ({len(new_doc['manifests'])} entries, "
        f"{len(warnings)} warning(s))"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
