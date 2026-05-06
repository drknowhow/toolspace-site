"""
Validate the structure of manifests.json.

This is a structural check on the index itself, not on the manifests it
points to (those should be validated by the install-manifest CLI before
inclusion). Catches things like missing keys, malformed URLs, duplicate
ids that would break the registry contract before deploy.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

SITE_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = SITE_ROOT / "manifests.json"

REQUIRED_TOP_KEYS = {"version", "schema_url", "manifests"}
REQUIRED_ENTRY_KEYS = {"id", "name", "description", "capabilities", "manifest_url", "status"}
ALLOWED_STATUS = {"example", "stable", "preview", "deprecated"}
REQUIRED_VERSION_KEYS = {"version", "schema_url", "status"}
ALLOWED_VERSION_STATUS = {"stable", "preview", "deprecated"}
KNOWN_MANIFEST_VERSIONS = {"0.1", "0.2", "0.3"}


def _is_https_url(s: str) -> bool:
    try:
        u = urlparse(s)
    except Exception:
        return False
    return u.scheme == "https" and bool(u.netloc)


def main() -> int:
    if not INDEX_PATH.is_file():
        print(f"missing: {INDEX_PATH}", file=sys.stderr)
        return 1

    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"manifests.json is not valid JSON: {e}", file=sys.stderr)
        return 1

    errors: list[str] = []

    missing_top = REQUIRED_TOP_KEYS - set(data.keys())
    if missing_top:
        errors.append(f"missing top-level keys: {sorted(missing_top)}")

    if not _is_https_url(data.get("schema_url", "")):
        errors.append(f"schema_url must be https URL, got: {data.get('schema_url')!r}")

    # Optional `versions` array: per-version schema URLs (added in 2026-05-05).
    # Backward-compatible — older indexes without this field still validate.
    versions = data.get("versions")
    if versions is not None:
        if not isinstance(versions, list) or not versions:
            errors.append("versions must be a non-empty array if present")
        else:
            seen_v: set[str] = set()
            for j, ver in enumerate(versions):
                vprefix = f"versions[{j}]"
                if not isinstance(ver, dict):
                    errors.append(f"{vprefix}: must be object")
                    continue
                miss_v = REQUIRED_VERSION_KEYS - set(ver.keys())
                if miss_v:
                    errors.append(f"{vprefix}: missing keys {sorted(miss_v)}")
                v = ver.get("version")
                if not isinstance(v, str) or not v:
                    errors.append(f"{vprefix}: version must be non-empty string")
                elif v in seen_v:
                    errors.append(f"{vprefix}: duplicate version {v!r}")
                else:
                    seen_v.add(v)
                if not _is_https_url(ver.get("schema_url", "")):
                    errors.append(f"{vprefix}: schema_url must be https URL")
                vstatus = ver.get("status")
                if vstatus not in ALLOWED_VERSION_STATUS:
                    errors.append(
                        f"{vprefix}: status must be one of {sorted(ALLOWED_VERSION_STATUS)}, "
                        f"got {vstatus!r}"
                    )

    manifests = data.get("manifests")
    if not isinstance(manifests, list):
        errors.append("manifests must be an array")
        manifests = []

    seen_ids: set[str] = set()
    for i, entry in enumerate(manifests):
        prefix = f"manifests[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{prefix}: must be object")
            continue
        missing = REQUIRED_ENTRY_KEYS - set(entry.keys())
        if missing:
            errors.append(f"{prefix}: missing keys {sorted(missing)}")
        eid = entry.get("id")
        if not isinstance(eid, str) or not eid:
            errors.append(f"{prefix}: id must be non-empty string")
        elif eid in seen_ids:
            errors.append(f"{prefix}: duplicate id {eid!r}")
        else:
            seen_ids.add(eid)
        caps = entry.get("capabilities")
        if not isinstance(caps, list) or not all(isinstance(c, str) for c in caps):
            errors.append(f"{prefix}: capabilities must be list[str]")
        if not _is_https_url(entry.get("manifest_url", "")):
            errors.append(f"{prefix}: manifest_url must be https URL")
        status = entry.get("status")
        if status not in ALLOWED_STATUS:
            errors.append(
                f"{prefix}: status must be one of {sorted(ALLOWED_STATUS)}, got {status!r}"
            )
        # Optional manifest_version field — if present, must match a known schema.
        mv = entry.get("manifest_version")
        if mv is not None and mv not in KNOWN_MANIFEST_VERSIONS:
            errors.append(
                f"{prefix}: manifest_version must be one of "
                f"{sorted(KNOWN_MANIFEST_VERSIONS)}, got {mv!r}"
            )

    if errors:
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        print(f"\nFAIL: {len(errors)} error(s) in manifests.json", file=sys.stderr)
        return 1

    print(f"ok: manifests.json valid ({len(manifests)} entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
