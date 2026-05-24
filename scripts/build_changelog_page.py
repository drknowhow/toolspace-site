"""
Build the human-browsable /changelog/ page from changelog.json.

Single source of truth for the changelog is changelog.json (schema
toolspace-changelog/1). This script renders changelog/index.html from
it so the two never drift.

Inline formatting inside changes[] strings uses a tiny markdown subset:
  `code`            → <code>code</code>
  [label](url)      → <a href="url">label</a>

Anything else is rendered as text (HTML-escaped). No bold/italic/etc.
Keep change strings prose-clean — feed consumers can render or display
the raw string with the markup intact.

Modes:
  --build   Regenerate changelog/index.html in place (default).
  --check   Regenerate to a tempdir; diff against the committed page.
            Exit 1 on drift. CI runs this on every PR.

Pattern mirrors scripts/build_registry_pages.py and sync_from_spec.py:
stdlib only, --check for CI, deterministic output.
"""

from __future__ import annotations

import argparse
import filecmp
import html
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

SITE_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG_JSON = SITE_ROOT / "changelog.json"
CHANGELOG_HTML = SITE_ROOT / "changelog" / "index.html"


# ---------------------------------------------------------------------------
# Markdown-lite renderer
# ---------------------------------------------------------------------------

# One regex that matches either a code span or a link. Order inside the
# alternation matters only for tie-breaks; the disjoint groups make the
# match unambiguous.
_TOKEN_RE = re.compile(r"`([^`]+)`|\[([^\]]+)\]\(([^)]+)\)")


def render_inline(text: str) -> str:
    """Render markdown-lite inline syntax → HTML. Escapes everything else."""
    out: list[str] = []
    pos = 0
    for m in _TOKEN_RE.finditer(text):
        if m.start() > pos:
            out.append(html.escape(text[pos : m.start()]))
        if m.group(1) is not None:
            out.append(f"<code>{html.escape(m.group(1))}</code>")
        else:
            label = m.group(2)
            href = m.group(3)
            # Recursively render the label so `code` inside [..] works.
            out.append(
                f'<a href="{html.escape(href, quote=True)}">{render_inline(label)}</a>'
            )
        pos = m.end()
    if pos < len(text):
        out.append(html.escape(text[pos:]))
    return "".join(out)


# ---------------------------------------------------------------------------
# Page renderer
# ---------------------------------------------------------------------------

PAGE_HEAD = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>changelog · toolspace</title>
  <meta name="description" content="A running log of what's shipped on toolspace.yepgent.com — version by version." />
  <meta name="color-scheme" content="dark light" />

  <!-- Machine-readable version of this page. -->
  <link rel="alternate" type="application/json" href="/changelog.json" title="toolspace changelog JSON" />

  <link rel="stylesheet" href="/style.css" />
  <style>
    .version-block {
      border-left: 3px solid var(--accent, #7cf5c4);
      padding: 0 0 0 1.25rem;
      margin-bottom: 2.5rem;
    }
    .version-header {
      display: flex;
      align-items: baseline;
      gap: 0.75rem;
      margin-bottom: 0.5rem;
      flex-wrap: wrap;
    }
    .version-tag {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 1rem;
      font-weight: 700;
      color: var(--accent, #7cf5c4);
    }
    .version-label {
      font-weight: 600;
      font-size: 1rem;
    }
    .version-date {
      font-size: 0.82rem;
      opacity: 0.55;
      margin-left: auto;
      font-variant-numeric: tabular-nums;
    }
    .version-changes {
      margin: 0.5rem 0 0;
      padding-left: 1.2rem;
      font-size: 0.93rem;
      line-height: 1.7;
    }
    .version-changes li {
      margin-bottom: 0.25rem;
    }
    .json-note {
      font-size: 0.82rem;
      opacity: 0.55;
      margin-top: 2.5rem;
    }
  </style>
</head>
<body>
  <header class="site-nav">
    <a class="brand" href="/">
      <svg class="brand-glyph" viewBox="0 0 56 56" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M14 6h22l10 10v34H14z" stroke="var(--accent)"/>
        <path d="M36 6v10h10" stroke="var(--accent)"/>
        <circle cx="40" cy="42" r="9" fill="var(--bg)" stroke="var(--accent)"/>
        <path d="M36 42.5l3 3 6-6.5" stroke="var(--accent)"/>
      </svg>
      <span>toolspace<span class="dot">.</span></span>
    </a>
    <nav>
      <a href="/">registry</a>
      <a href="/changelog/">changelog</a>
      <a href="https://github.com/drknowhow/install-manifest-spec">spec</a>
      <a href="https://yepgent.com/">yepgent</a>
    </nav>
  </header>

  <main>
    <h1>Changelog</h1>
    <p class="lede">A running log of what's shipped on toolspace.yepgent.com. Newest first.</p>

    <hr />

"""

PAGE_TAIL_TEMPLATE = """    <p class="json-note">Machine-readable: <a href="/changelog.json">changelog.json</a></p>

    <footer>
      <p>
        toolspace.yepgent.com ·
        <span class="muted">v{footer_version}</span> ·
        <a href="/changelog/">changelog</a> ·
        <a href="https://github.com/drknowhow/toolspace-site">source</a> ·
        <a href="https://github.com/drknowhow/install-manifest-spec">spec</a> ·
        <a href="https://yepgent.com">yepgent</a>
      </p>
    </footer>
  </main>
</body>
</html>
"""


def render_version_block(entry: dict) -> str:
    version = entry["version"]
    label = entry.get("label", "")
    date = entry.get("date", "")
    changes = entry.get("changes", [])

    items = "\n".join(
        f"        <li>{render_inline(c)}</li>" for c in changes
    )
    return (
        f'    <div class="version-block">\n'
        f'      <div class="version-header">\n'
        f'        <span class="version-tag">v{html.escape(version)}</span>\n'
        f'        <span class="version-label">{render_inline(label)}</span>\n'
        f'        <span class="version-date">{html.escape(date)}</span>\n'
        f"      </div>\n"
        f'      <ul class="version-changes">\n'
        f"{items}\n"
        f"      </ul>\n"
        f"    </div>\n"
    )


def render_page(data: dict) -> str:
    versions = data.get("versions", [])
    footer_version = data.get("current_version", "")
    blocks = "\n".join(render_version_block(v) for v in versions)
    return PAGE_HEAD + blocks + "\n" + PAGE_TAIL_TEMPLATE.format(
        footer_version=html.escape(footer_version)
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build(out_path: Path) -> None:
    data = json.loads(CHANGELOG_JSON.read_text(encoding="utf-8"))
    page = render_page(data)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Render to a tempdir and diff against the committed page; exit 1 on drift.",
    )
    args = parser.parse_args(argv)

    if not CHANGELOG_JSON.is_file():
        print(f"missing: {CHANGELOG_JSON}", file=sys.stderr)
        return 1

    if args.check:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "changelog" / "index.html"
            build(tmp)
            if not CHANGELOG_HTML.is_file():
                print(f"missing committed page: {CHANGELOG_HTML}", file=sys.stderr)
                return 1
            if not filecmp.cmp(tmp, CHANGELOG_HTML, shallow=False):
                print(
                    "DRIFT: changelog/index.html does not match a fresh build "
                    "from changelog.json. Run `python scripts/build_changelog_page.py` "
                    "and commit the result.",
                    file=sys.stderr,
                )
                # Print a small diff for the reviewer.
                a = tmp.read_text(encoding="utf-8").splitlines()
                b = CHANGELOG_HTML.read_text(encoding="utf-8").splitlines()
                import difflib

                for line in difflib.unified_diff(
                    b, a, fromfile="committed", tofile="fresh-build", lineterm=""
                ):
                    print(line, file=sys.stderr)
                return 1
            print("ok: changelog/index.html matches a fresh build")
            return 0

    build(CHANGELOG_HTML)
    print(f"wrote: {CHANGELOG_HTML.relative_to(SITE_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
