"""Guard: index.html's 'What a manifest looks like' snippet tracks the spec.

The spec README's version table is the single source of truth for which
schema version this site advertises. When the spec bumps `current`, the
homepage snippet and its linked schema URL must follow. This test
exercises the parser + drift detector in scripts/sync_from_spec.py
against synthetic spec READMEs so the next version bump fails CI loudly
instead of shipping a stale snippet (which is what happened pre-PR #38
when the snippet still said 0.3 after v0.4 went live).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import sync_from_spec  # noqa: E402


def _make_spec(tmp_path: Path, current: str, others: list[str] | None = None) -> Path:
    """Write a minimal spec repo with a version table marking `current`."""
    others = others or []
    rows = [f"| **{current}** | current | [`schema/x.json`](x) | [`x.md`](x) | x |"]
    rows += [
        f"| **{v}** | superseded by {current} | [`schema/x.json`](x) | [`x.md`](x) | x |"
        for v in others
    ]
    readme = tmp_path / "README.md"
    readme.write_text(
        "# install-manifest spec\n\n## Versions\n\n"
        "| Version | Status | Schema | Design notes | What's new |\n"
        "|---|---|---|---|---|\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    return tmp_path


def test_reads_current_version_from_spec_readme(tmp_path):
    spec = _make_spec(tmp_path, current="0.4", others=["0.3.1", "0.3"])
    assert sync_from_spec._read_spec_current_version(spec) == "0.4"


def test_future_version_bump_is_parsed(tmp_path):
    """When the spec bumps to 0.5, the parser picks it up automatically."""
    spec = _make_spec(tmp_path, current="0.5", others=["0.4"])
    assert sync_from_spec._read_spec_current_version(spec) == "0.5"


def test_homepage_passes_when_aligned_with_spec():
    """The live index.html on this branch advertises whatever spec says is current.

    If this fails locally, run:
        python scripts/sync_from_spec.py --check --spec-path ../install-manifest-spec
    and follow the printed instructions to bring the homepage back in line.
    """
    spec_root = REPO_ROOT.parent / "install-manifest-spec"
    if not spec_root.is_dir():
        # CI checks out the spec as a sibling; locally it may not exist.
        return
    current = sync_from_spec._read_spec_current_version(spec_root)
    drift = sync_from_spec._check_homepage_advertises_current(current)
    assert drift == [], f"index.html drifts from spec v{current}: {drift}"


def test_homepage_drift_is_detected_when_spec_advances(monkeypatch):
    """If the spec is at v0.5 but index.html still says 0.4, guard must fire."""
    # Pretend the spec's current version is one ahead of whatever index.html
    # currently advertises. The guard should report two drift messages: one
    # for the snippet version, one for the schema link.
    drift = sync_from_spec._check_homepage_advertises_current("99.99")
    assert len(drift) == 2
    assert any("manifest_version" in msg for msg in drift)
    assert any("install-manifest-v99.99.json" in msg for msg in drift)
