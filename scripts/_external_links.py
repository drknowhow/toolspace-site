"""Mark external anchors to open in a new tab.

Shared by the page generators (build_registry_pages, build_changelog_page)
and applied once to the hand-authored static pages. An anchor is "external"
when its href is an absolute http(s) URL whose host is not this site
(toolspace.yepgent.com). External anchors get target="_blank" and a
rel that includes noopener + noreferrer (security: a new-tab link must
not give the opened page window.opener access).

The transform is idempotent: anchors that already declare target= are left
alone, and rel merging is order-independent, so re-running (every build,
plus the one-time static pass) is safe.

stdlib only.
"""
from __future__ import annotations

import re

# Hosts considered "this site" — links to these stay in the same tab.
INTERNAL_HOSTS = {"toolspace.yepgent.com"}

_A_OPEN = re.compile(r"<a\b([^>]*)>", re.IGNORECASE)
_HREF = re.compile(r'href\s*=\s*"([^"]*)"', re.IGNORECASE)
_HAS_TARGET = re.compile(r"\btarget\s*=", re.IGNORECASE)
_REL = re.compile(r'\brel\s*=\s*"([^"]*)"', re.IGNORECASE)


def _is_external(href: str) -> bool:
    m = re.match(r"https?://([^/]+)", href.strip(), re.IGNORECASE)
    if not m:
        return False  # relative, #anchor, mailto:, tel: — all internal/non-nav
    host = m.group(1).split(":", 1)[0].lower()
    return host not in INTERNAL_HOSTS


def _rewrite_open_tag(attrs: str) -> str:
    href_m = _HREF.search(attrs)
    if not href_m or not _is_external(href_m.group(1)):
        return "<a" + attrs + ">"

    new = attrs
    if not _HAS_TARGET.search(new):
        new = new + ' target="_blank"'

    rel_m = _REL.search(new)
    if rel_m:
        vals = set(rel_m.group(1).split())
        vals.update({"noopener", "noreferrer"})
        rel_attr = 'rel="' + " ".join(sorted(vals)) + '"'
        new = new[: rel_m.start()] + rel_attr + new[rel_m.end():]
    else:
        new = new + ' rel="noopener noreferrer"'

    return "<a" + new + ">"


def mark_external_links(html: str) -> str:
    """Return html with external anchors set to open in a new tab."""
    return _A_OPEN.sub(lambda m: _rewrite_open_tag(m.group(1)), html)
