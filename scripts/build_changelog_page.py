"""
Build the human-browsable /changelog/ page from changelog.json.

Single source of truth for the changelog is changelog.json (schema
toolspace-changelog/1). This script renders changelog/index.html from
it so the two never drift.

The page uses the shared yepgent v4 design language: it sets
`body class="home-v4 changelog-v4"` and reuses the v4 shell (glass pill
nav, aurora hero, reveal motion) plus the `changelog-v4`/`cv4-*` layer
that lives in style.css — mirrored from yepgent.com/changelog so the two
sites read the same.

Inline formatting inside changes[]/label strings uses a tiny markdown
subset:
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
# Page renderer — v4 design (body.home-v4 .changelog-v4)
# ---------------------------------------------------------------------------

PAGE_HEAD = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>changelog &middot; toolspace</title>
  <meta name="description" content="A running log of what's shipped on toolspace.yepgent.com — version by version." />
  <meta name="color-scheme" content="dark light" />

  <!-- Machine-readable version of this page. -->
  <link rel="alternate" type="application/json" href="/changelog.json" title="toolspace changelog JSON" />

  <!-- Shared yepgent theme; the changelog-v4 layer lives here too. -->
  <link rel="stylesheet" href="/style.css" />
  <link rel="canonical" href="https://toolspace.yepgent.com/changelog/" />
</head>
<body class="home-v4 changelog-v4">

  <header class="site-nav v4">
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
      <a href="/registry/">registry</a>
      <a href="/publish/">publish</a>
      <a href="/changelog/" aria-current="page">changelog</a>
      <a href="https://yepgent.com/">yepgent</a>
      <a href="https://github.com/drknowhow/install-manifest-spec">spec</a>
    </nav>
  </header>

  <main class="v4-shell">

    <!-- HERO -->
    <section class="cv4-hero">
      <div class="aurora" aria-hidden="true">
        <span class="aurora-1"></span>
        <span class="aurora-2"></span>
      </div>

      <div class="cv4-hero-stage">
        <p class="v4-eyebrow">
          <span class="eyebrow-tick" aria-hidden="true"></span>
          changelog &middot; newest first
        </p>
        <h1 class="cv4-display">
          What <span class="serif-italic">shipped.</span>
        </h1>
        <p class="cv4-lede">
          A running log of changes to toolspace.yepgent.com &mdash; version by version.
          Machine-readable mirror at <a href="/changelog.json"><code>changelog.json</code></a>.
        </p>
      </div>
    </section>

    <!-- LOG -->
    <section class="cv4-log-wrap">
      <ol class="cv4-log">

"""

PAGE_TAIL = """
      </ol>

      <p class="cv4-json-note">Machine-readable: <a href="/changelog.json">changelog.json</a></p>
    </section>

    <!-- COLOPHON -->
    <footer class="v4-colophon">
      <p>
        toolspace.yepgent.com &middot;
        <a href="/registry/">registry</a> &middot;
        <a href="https://github.com/drknowhow/toolspace-site">source</a> &middot;
        <a href="https://github.com/drknowhow/install-manifest-spec">spec</a> &middot;
        <a href="https://yepgent.com">yepgent</a>
      </p>
    </footer>
  </main>

  <script>
  /* Nav scroll-compaction — mirror of the home/changelog v4 nav. */
  (function () {
    const nav = document.querySelector('header.site-nav.v4');
    if (!nav) return;
    let last = 0;
    const onScroll = () => {
      const y = window.scrollY || 0;
      if (y > 12 && last <= 12) nav.classList.add('scrolled');
      else if (y <= 12 && last > 12) nav.classList.remove('scrolled');
      last = y;
    };
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
  })();

  /* Reveal-on-scroll for the hero + release cards. */
  (function () {
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const items = document.querySelectorAll('.cv4-hero-stage, .cv4-entry, .v4-colophon');
    if (reduce || !('IntersectionObserver' in window)) {
      items.forEach((el) => el.classList.add('is-in'));
      return;
    }
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting) { e.target.classList.add('is-in'); io.unobserve(e.target); }
      });
    }, { rootMargin: '0px 0px 20% 0px', threshold: 0.01 });
    items.forEach((el) => io.observe(el));
  })();
  </script>
</body>
</html>
"""


def render_entry(entry: dict) -> str:
    version = entry["version"]
    label = entry.get("label", "")
    date = entry.get("date", "")
    changes = entry.get("changes", [])

    items = "\n".join(
        f"            <li>{render_inline(c)}</li>" for c in changes
    )
    return (
        f'        <li class="cv4-entry">\n'
        f'          <div class="cv4-entry-head">\n'
        f'            <span class="cv4-tag">v{html.escape(version)}</span>\n'
        f'            <span class="cv4-label">{render_inline(label)}</span>\n'
        f'            <time class="cv4-date" datetime="{html.escape(date, quote=True)}">'
        f"{html.escape(date)}</time>\n"
        f"          </div>\n"
        f'          <ul class="cv4-changes">\n'
        f"{items}\n"
        f"          </ul>\n"
        f"        </li>\n"
    )


def render_page(data: dict) -> str:
    versions = data.get("versions", [])
    entries = "\n".join(render_entry(v) for v in versions)
    return PAGE_HEAD + entries + PAGE_TAIL


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
