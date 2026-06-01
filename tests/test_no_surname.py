"""Guard: no surname leaks anywhere on the public site.

Public-facing surfaces — examples/, registry/, manifests.json, _cache/ —
ship verbatim to https://toolspace.yepgent.com. The maintainer's
public_identity rule says public surfaces use "Dimitri T", never the
full last name. This test fails on any occurrence of the surname in
tracked files.

Catches the 2026-06-01 Mira class of breach: author.name fields in
manifests propagating through build_registry_pages.py into the public
"by X" line on the registry page.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SURNAME = "Tselenchuk"

# Directories whose contents ship to public surfaces.
SCANNED_DIRS = [
    REPO_ROOT / "examples",
    REPO_ROOT / "registry",
    REPO_ROOT / "_cache",
    REPO_ROOT / "schemas",
    REPO_ROOT / "manifests",
]

SCANNED_FILES = [
    REPO_ROOT / "manifests.json",
    REPO_ROOT / "publishers.json",
    REPO_ROOT / "index.html",
    REPO_ROOT / "README.md",
]

EXTENSIONS = {".json", ".html", ".md", ".txt"}

# Files in scanned dirs that are documented exceptions. Keep empty.
ALLOWLIST: set[Path] = set()


def _public_files() -> list[Path]:
    files: list[Path] = []
    for d in SCANNED_DIRS:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.is_file() and p.suffix.lower() in EXTENSIONS:
                files.append(p)
    for f in SCANNED_FILES:
        if f.exists() and f.is_file():
            files.append(f)
    return files


def test_no_surname_in_public_files() -> None:
    offenders: list[tuple[str, int]] = []
    for path in _public_files():
        if path in ALLOWLIST:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if SURNAME in text:
            offenders.append((str(path.relative_to(REPO_ROOT)), text.count(SURNAME)))

    if offenders:
        lines = [f"  {p}: {n} hit(s)" for p, n in offenders]
        raise AssertionError(
            f"Surname '{SURNAME}' found in public-facing files:\n"
            + "\n".join(lines)
            + "\n\nUse 'Dimitri T' or 'Yep (agent on behalf of Dimitri T)'. "
            "If the leak is in _cache/ or registry/, the upstream manifest "
            "at yepgent.com is the root cause; fix it there first, then "
            "re-run scripts/build_registry_pages.py."
        )
