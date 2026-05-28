"""End-to-end tests for the federation pipeline.

Covers:

* `federation.validate_well_known_index` — schema constraints
* `federation.discovery_url_for` — per-kind URL synthesis
* `federation.source_url_from_raw` — github raw → blob URL derivation
* `federation.map_federation_status_to_registry` — status mapping incl.
  publisher.registry_status_map override
* `discover_publishers._discover_one` — fixture-mode discovery
* `sync_from_publishers.build_synced_index` — end-to-end merge,
  preserving non-federation entries + dropping broken/quarantined

stdlib + pytest only. No third-party libraries.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

sys.path.insert(0, str(SCRIPTS_DIR))

import federation  # noqa: E402
import discover_publishers  # noqa: E402
import sync_from_publishers  # noqa: E402


# ---- federation.validate_well_known_index ---------------------------------


def _muninn_index() -> dict:
    path = FIXTURES_DIR / (
        "https_raw.githubusercontent.com__oaustegard__muninn-utilities__main__"
        ".well-known__install-manifests.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_validate_accepts_fixture():
    errors = federation.validate_well_known_index(_muninn_index())
    assert errors == [], errors


def test_validate_rejects_wrong_top_version():
    doc = _muninn_index()
    doc["version"] = "2"
    errors = federation.validate_well_known_index(doc)
    assert any("version" in e for e in errors)


def test_validate_rejects_bad_github_id():
    doc = _muninn_index()
    doc["publisher"]["id"] = "nope-no-slash"
    errors = federation.validate_well_known_index(doc)
    assert any("publisher.id" in e for e in errors)


def test_validate_accepts_https_kind_with_hostname():
    doc = _muninn_index()
    doc["publisher"]["kind"] = "https"
    doc["publisher"]["id"] = "muninn.example.com"
    errors = federation.validate_well_known_index(doc)
    assert errors == [], errors


def test_validate_rejects_atproto_without_record_uri():
    doc = _muninn_index()
    doc["publisher"]["kind"] = "atproto"
    doc["publisher"]["id"] = "did:plc:abcdefghijklmnopqrstuvwx"
    errors = federation.validate_well_known_index(doc)
    assert any("atproto_record_uri" in e for e in errors)


def test_validate_rejects_deprecated_without_target():
    doc = _muninn_index()
    # The fixture's deprecated entry has the target; strip it.
    for m in doc["manifests"]:
        if m["status"] == "deprecated":
            m.pop("deprecated_in_favor_of", None)
    errors = federation.validate_well_known_index(doc)
    assert any("deprecated_in_favor_of" in e for e in errors)


def test_validate_rejects_unknown_manifest_version():
    doc = _muninn_index()
    doc["manifests"][0]["manifest_version"] = "0.99"
    errors = federation.validate_well_known_index(doc)
    assert any("manifest_version" in e for e in errors)


def test_validate_rejects_status_outside_enum():
    doc = _muninn_index()
    doc["manifests"][0]["status"] = "experimental"
    errors = federation.validate_well_known_index(doc)
    assert any("status" in e for e in errors)


def test_validate_rejects_duplicate_ids():
    doc = _muninn_index()
    doc["manifests"].append(copy.deepcopy(doc["manifests"][0]))
    errors = federation.validate_well_known_index(doc)
    assert any("duplicate" in e for e in errors)


# ---- federation.discovery_url_for -----------------------------------------


def test_discovery_url_github():
    url = federation.discovery_url_for(
        {"kind": "github", "id": "oaustegard/muninn-utilities"}
    )
    assert url == (
        "https://raw.githubusercontent.com/oaustegard/muninn-utilities/main/"
        ".well-known/install-manifests.json"
    )


def test_discovery_url_https():
    url = federation.discovery_url_for({"kind": "https", "id": "muninn.example.com"})
    assert url == "https://muninn.example.com/.well-known/install-manifests.json"


def test_discovery_url_unknown_kind_raises():
    with pytest.raises(ValueError):
        federation.discovery_url_for({"kind": "ftp", "id": "x"})


# ---- federation.source_url_from_raw ---------------------------------------


def test_source_url_derivation():
    raw = (
        "https://raw.githubusercontent.com/oaustegard/muninn-utilities/main/"
        "manifests/bsky-card/muninn-bsky-card.v0.4.json"
    )
    src = federation.source_url_from_raw(raw)
    assert src == (
        "https://github.com/oaustegard/muninn-utilities/blob/main/"
        "manifests/bsky-card/muninn-bsky-card.v0.4.json"
    )


def test_source_url_passthrough_for_non_github():
    src = federation.source_url_from_raw("https://example.com/foo.json")
    assert src == "https://example.com/foo.json"


# ---- federation.map_federation_status_to_registry -------------------------


def test_status_map_default_active_to_stable():
    assert (
        federation.map_federation_status_to_registry("active", {"trust_tier": "standard"})
        == "stable"
    )


def test_status_map_default_deprecated_to_deprecated():
    assert (
        federation.map_federation_status_to_registry(
            "deprecated", {"trust_tier": "standard"}
        )
        == "deprecated"
    )


def test_status_map_broken_skips():
    assert federation.map_federation_status_to_registry("broken", {}) is None
    assert federation.map_federation_status_to_registry("quarantined", {}) is None


def test_status_map_override_via_publisher():
    pub = {"trust_tier": "verified", "registry_status_map": {"active": "example"}}
    assert (
        federation.map_federation_status_to_registry("active", pub) == "example"
    )


# ---- discover_publishers --------------------------------------------------


@pytest.fixture
def fixtures_env(monkeypatch):
    monkeypatch.setenv("TOOLSPACE_FEDERATION_FIXTURES", str(FIXTURES_DIR))
    yield FIXTURES_DIR


def test_discover_one_muninn_ok(fixtures_env, monkeypatch):
    """Patch federation.fetch_url to use the fixture loader."""
    def stub(url, timeout=15.0):
        return sync_from_publishers._fetch_with_fixtures(url)

    monkeypatch.setattr(federation, "fetch_url", stub)
    monkeypatch.setattr(discover_publishers, "fetch_url", stub)

    publisher = {
        "kind": "github",
        "id": "oaustegard/muninn-utilities",
        "display_name": "Muninn Utilities",
    }
    result = discover_publishers._discover_one(publisher)
    assert result["ok"], result.get("error")
    assert result["manifest_count"] == 3
    assert "muninn-bsky-card" in result["manifest_ids"]


# ---- sync_from_publishers end-to-end --------------------------------------


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Stage a writable copy of publishers.json + manifests.json under tmp_path."""
    site = tmp_path / "site"
    site.mkdir()
    # Use the test fixture publishers list (just Muninn).
    shutil.copy(FIXTURES_DIR / "publishers.json", site / "publishers.json")
    # Start with a manifests.json that has one preserved non-federation entry
    # and one stale federation entry (to verify drop-and-rebuild behavior).
    seed = {
        "version": "1",
        "schema_url": "https://toolspace.yepgent.com/schemas/install-manifest-v0.4.json",
        "versions": [],
        "generated_at": "2026-01-01",
        "manifests": [
            {
                "id": "gmail-example",
                "name": "Gmail Example",
                "description": "Preserved non-federation entry.",
                "capabilities": ["email"],
                "manifest_url": "https://example.com/gmail.json",
                "source": "https://example.com/gmail.json",
                "manifest_version": "0.1",
                "status": "example"
            },
            {
                "id": "stale-federated",
                "name": "Stale",
                "description": "Stale federated entry that should be dropped.",
                "capabilities": [],
                "manifest_url": "https://example.com/stale.json",
                "source": "https://example.com/stale.json",
                "manifest_version": "0.4",
                "status": "stable",
                "federation": {
                    "publisher_kind": "github",
                    "publisher_id": "ghosts/none",
                    "trust_tier": "standard"
                }
            }
        ]
    }
    (site / "manifests.json").write_text(json.dumps(seed, indent=2), encoding="utf-8")

    monkeypatch.setattr(sync_from_publishers, "PUBLISHERS_PATH", site / "publishers.json")
    monkeypatch.setattr(sync_from_publishers, "MANIFESTS_PATH", site / "manifests.json")
    monkeypatch.setenv("TOOLSPACE_FEDERATION_FIXTURES", str(FIXTURES_DIR))
    yield site


def test_sync_preserves_non_federation_entries(sandbox):
    new_doc, warnings = sync_from_publishers.build_synced_index()
    ids = [m["id"] for m in new_doc["manifests"]]
    assert "gmail-example" in ids


def test_sync_drops_stale_federated_entries(sandbox):
    new_doc, warnings = sync_from_publishers.build_synced_index()
    ids = [m["id"] for m in new_doc["manifests"]]
    assert "stale-federated" not in ids


def test_sync_emits_federated_entries(sandbox):
    new_doc, warnings = sync_from_publishers.build_synced_index()
    fed_ids = [
        m["id"] for m in new_doc["manifests"] if "federation" in m
    ]
    assert "muninn-bsky-card" in fed_ids
    assert "muninn-old-tool" in fed_ids  # deprecated → deprecated, still included


def test_sync_skips_broken_federation_status(sandbox):
    new_doc, warnings = sync_from_publishers.build_synced_index()
    ids = [m["id"] for m in new_doc["manifests"]]
    assert "muninn-broken-tool" not in ids


def test_sync_federation_entry_shape(sandbox):
    new_doc, warnings = sync_from_publishers.build_synced_index()
    fed = next(m for m in new_doc["manifests"] if m["id"] == "muninn-bsky-card")
    assert fed["name"] == "Muninn Bluesky Card"  # from fetched manifest's tool.name
    assert fed["description"].startswith("Compose and publish")  # tool.summary
    assert "bluesky" in fed["capabilities"]  # tool.tags
    assert fed["manifest_version"] == "0.4"
    assert fed["status"] == "stable"
    assert fed["federation"]["publisher_kind"] == "github"
    assert fed["federation"]["publisher_id"] == "oaustegard/muninn-utilities"
    assert (
        fed["source"]
        == "https://github.com/oaustegard/muninn-utilities/blob/main/manifests/bsky-card/muninn-bsky-card.v0.4.json"
    )


def test_sync_deprecated_status_propagates(sandbox):
    new_doc, warnings = sync_from_publishers.build_synced_index()
    old = next(m for m in new_doc["manifests"] if m["id"] == "muninn-old-tool")
    assert old["status"] == "deprecated"


def test_sync_no_warnings_for_clean_run(sandbox):
    new_doc, warnings = sync_from_publishers.build_synced_index()
    # Broken entries fail at the manifest_url fetch step (no fixture exists);
    # that surfaces as a warning. The two healthy manifests should sync cleanly.
    # Verify warnings are scoped to the broken-fetch case specifically.
    assert all("broken" in w or "muninn-broken" in w for w in warnings), warnings
