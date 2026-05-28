"""
Shared federation helpers (stdlib-only).

Defines:

* `PUBLISHER_KINDS`, the three identity kinds the registry consumes.
* `discovery_url_for(publisher)`, mapping (kind, id) to a fetchable URL.
* `validate_well_known_index(doc)`, an inline structural check for
  well-known-index-v1 documents (no jsonschema dep, matching the
  repo's deliberate-stdlib stance).
* `fetch_url(url, timeout)`, a small URL fetcher with a sensible
  User-Agent and size cap.
* `map_federation_status_to_registry(...)`, the registry's lifecycle
  mapping from federation status + publisher trust tier to the
  status enum manifests.json carries.

The well-known-index schema lives at
  https://toolspace.yepgent.com/schemas/well-known-index-v1.json
and is also mirrored byte-identical into this repo at
  schemas/well-known-index-v1.json
once the install-manifest-spec PR for v1 ships. The structural
validator below covers the same constraints; full JSON Schema
validation by registries that want it is layered on top.

The well-known-index validator below is stdlib-only, matching the
site-wide convention (see validate_manifests_index.py). The install-
manifest BODY validator lives in `sync_from_publishers._fetch_install_
manifest` and DOES depend on the published `install-manifest` package
(see requirements-federation.txt) so fetched bodies are checked against
the same schema the canonical CLI uses — fixing the validator-
divergence bug surfaced 2026-05-28.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

PUBLISHER_KINDS = ("github", "https", "atproto")

GITHUB_ID_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}/[A-Za-z0-9._-]{1,100}$"
)
HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)
DID_RE = re.compile(r"^did:(plc:[a-z0-9]{24}|web:[a-z0-9.-]+)$")
TOOL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
TAG_RE = re.compile(r"^[a-z0-9-]+$")

ALLOWED_INDEX_STATUS = {"active", "deprecated", "broken", "quarantined"}
KNOWN_MANIFEST_VERSIONS = {"0.1", "0.2", "0.3", "0.3.1", "0.4"}
USER_AGENT = "toolspace-yepgent-federation/1.0 (+https://toolspace.yepgent.com)"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MiB — well-known indexes stay small


def discovery_url_for(publisher: dict) -> str:
    """Map (kind, id) to the URL where the well-known index lives.

    Atproto publishers' canonical record lives at the PDS at-uri (in
    publisher.atproto_record_uri); for v1 we still fetch via HTTPS at
    the publisher's homepage-derived discovery URL. Resolving the
    at-uri properly is a follow-up when the AT Protocol lexicon
    `app.toolspace.installManifests` ships.
    """
    kind = publisher.get("kind")
    pid = publisher.get("id", "")
    if kind == "github":
        return (
            f"https://raw.githubusercontent.com/{pid}/main/.well-known/install-manifests.json"
        )
    if kind == "https":
        return f"https://{pid}/.well-known/install-manifests.json"
    if kind == "atproto":
        # v1 placeholder — registries will fetch the at-uri record once
        # the AT lexicon is published. For now, atproto publishers are
        # rejected at discover time with a clear error.
        return ""
    raise ValueError(f"unknown publisher kind: {kind!r}")


def source_url_from_raw(manifest_url: str) -> str:
    """Derive a github.com blob URL from a raw.githubusercontent.com URL.

    Returns the original URL if the input is not a GitHub raw URL —
    callers fall back to manifest_url for the registry's `source` field.
    """
    # https://raw.githubusercontent.com/<owner>/<repo>/<ref>/<path>
    # ->
    # https://github.com/<owner>/<repo>/blob/<ref>/<path>
    prefix = "https://raw.githubusercontent.com/"
    if not manifest_url.startswith(prefix):
        return manifest_url
    tail = manifest_url[len(prefix):]
    parts = tail.split("/", 3)
    if len(parts) < 4:
        return manifest_url
    owner, repo, ref, path = parts
    return f"https://github.com/{owner}/{repo}/blob/{ref}/{path}"


def validate_well_known_index(doc: Any) -> list[str]:
    """Structural validator for well-known-index v1.

    Returns a list of error messages; empty list means valid. Mirrors
    the schema's constraints without pulling in jsonschema.
    """
    errors: list[str] = []
    if not isinstance(doc, dict):
        return ["root: must be object"]

    # Top-level required + const
    if doc.get("version") != "1":
        errors.append(f"version: must be const '1', got {doc.get('version')!r}")

    for k in ("publisher", "generated_at", "manifests"):
        if k not in doc:
            errors.append(f"missing top-level key: {k}")

    publisher = doc.get("publisher")
    if not isinstance(publisher, dict):
        errors.append("publisher: must be object")
        publisher = {}

    kind = publisher.get("kind")
    pid = publisher.get("id", "")
    if kind not in PUBLISHER_KINDS:
        errors.append(f"publisher.kind: must be one of {sorted(PUBLISHER_KINDS)}, got {kind!r}")

    if not isinstance(pid, str) or not pid:
        errors.append("publisher.id: must be non-empty string")
    else:
        if kind == "github" and not GITHUB_ID_RE.fullmatch(pid):
            errors.append(f"publisher.id: kind=github requires owner/repo, got {pid!r}")
        elif kind == "https" and not HOSTNAME_RE.fullmatch(pid):
            errors.append(f"publisher.id: kind=https requires bare hostname, got {pid!r}")
        elif kind == "atproto" and not DID_RE.fullmatch(pid):
            errors.append(f"publisher.id: kind=atproto requires did:plc:/did:web:, got {pid!r}")

    display_name = publisher.get("display_name")
    if not isinstance(display_name, str) or not display_name:
        errors.append("publisher.display_name: must be non-empty string")
    elif len(display_name) > 80:
        errors.append(f"publisher.display_name: max 80 chars, got {len(display_name)}")

    if kind == "atproto" and "atproto_record_uri" not in publisher:
        errors.append("publisher.atproto_record_uri: required when kind=atproto")

    if "atproto_record_uri" in publisher:
        uri = publisher["atproto_record_uri"]
        if not isinstance(uri, str) or not uri.startswith("at://"):
            errors.append("publisher.atproto_record_uri: must start with 'at://'")

    manifests = doc.get("manifests")
    if not isinstance(manifests, list):
        errors.append("manifests: must be array")
        manifests = []

    seen: set[str] = set()
    for i, entry in enumerate(manifests):
        prefix = f"manifests[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{prefix}: must be object")
            continue
        for k in ("id", "manifest_url", "manifest_version", "status"):
            if k not in entry:
                errors.append(f"{prefix}: missing required key {k!r}")

        eid = entry.get("id", "")
        if not isinstance(eid, str) or not TOOL_ID_RE.fullmatch(eid):
            errors.append(f"{prefix}.id: must match {TOOL_ID_RE.pattern}, got {eid!r}")
        elif eid in seen:
            errors.append(f"{prefix}.id: duplicate within publisher: {eid!r}")
        else:
            seen.add(eid)

        url = entry.get("manifest_url", "")
        if not isinstance(url, str) or not (url.startswith("https://") or url.startswith("http://")):
            errors.append(f"{prefix}.manifest_url: must be https URL, got {url!r}")

        mv = entry.get("manifest_version")
        if mv not in KNOWN_MANIFEST_VERSIONS:
            errors.append(
                f"{prefix}.manifest_version: must be one of "
                f"{sorted(KNOWN_MANIFEST_VERSIONS)}, got {mv!r}"
            )

        status = entry.get("status")
        if status not in ALLOWED_INDEX_STATUS:
            errors.append(
                f"{prefix}.status: must be one of {sorted(ALLOWED_INDEX_STATUS)}, "
                f"got {status!r}"
            )

        if status == "deprecated" and "deprecated_in_favor_of" not in entry:
            errors.append(f"{prefix}: status='deprecated' requires deprecated_in_favor_of")

        if "deprecated_in_favor_of" in entry:
            tgt = entry["deprecated_in_favor_of"]
            if not isinstance(tgt, str) or not TOOL_ID_RE.fullmatch(tgt):
                errors.append(
                    f"{prefix}.deprecated_in_favor_of: must match tool id pattern, got {tgt!r}"
                )

        summary = entry.get("summary")
        if summary is not None:
            if not isinstance(summary, str) or not (1 <= len(summary) <= 280):
                errors.append(f"{prefix}.summary: must be 1..280 chars when present")

        tags = entry.get("tags")
        if tags is not None:
            if not isinstance(tags, list) or len(tags) > 16:
                errors.append(f"{prefix}.tags: must be array of ≤16 strings")
            else:
                for t in tags:
                    if not isinstance(t, str) or not TAG_RE.fullmatch(t):
                        errors.append(f"{prefix}.tags: each tag must match {TAG_RE.pattern}")

    return errors


def fetch_url(url: str, timeout: float = 15.0) -> bytes:
    """Fetch a URL with a sane User-Agent and size cap.

    Raises urllib.error.URLError / HTTPError on failure, ValueError on
    oversized response. Callers are expected to handle errors.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError(f"response exceeded {MAX_RESPONSE_BYTES} bytes: {url}")
    return raw


def map_federation_status_to_registry(
    federation_status: str,
    publisher: dict,
) -> str | None:
    """Map a federation index entry's status + publisher trust to manifests.json status.

    Returns None when the entry should be skipped (broken, quarantined,
    or unrecognized status).

    publisher.registry_status_map (optional) overrides the default mapping.
    Used by the drknowhow/Yep self-dogfood entry to map 'active' to
    'example' rather than 'stable' — preserves visual separation between
    Yep's own tools and third-party publisher catalogs.
    """
    override = (publisher or {}).get("registry_status_map") or {}
    if federation_status in override:
        return override[federation_status]

    default_map = {
        "active": "stable",
        "deprecated": "deprecated",
        "broken": None,
        "quarantined": None,
    }
    return default_map.get(federation_status)
