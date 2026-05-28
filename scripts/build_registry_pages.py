"""
Build the human-browsable registry pages from manifests.json.

Generates:
  registry/index.html              — marketplace card grid + filter chips
  registry/<id>/index.html         — per-manifest product page

Reads each entry's manifest_url, fetches the JSON, renders the page,
caches the fetched JSON to _cache/registry/<id>.json. The cache is
gitignored — it's strictly a network-resiliency / build-determinism aid.

Modes:
  --build   Regenerate registry/ in place (default).
  --check   Regenerate to a tempdir; diff against the committed pages.
            Exit 1 on drift. CI runs this on every PR.

Pattern mirrors scripts/sync_from_spec.py: stdlib only, --check for CI.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import filecmp
import hashlib
import html
import json
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

SITE_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = SITE_ROOT / "manifests.json"
REGISTRY_DIR = SITE_ROOT / "registry"
CACHE_DIR = SITE_ROOT / "_cache" / "registry"

USER_AGENT = "toolspace-build/0.1 (+https://toolspace.yepgent.com/)"
FETCH_TIMEOUT = 10  # seconds


# ---------------------------------------------------------------------------
# Fetch + cache
# ---------------------------------------------------------------------------

def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return resp.read()


SELF_ORIGIN = "https://toolspace.yepgent.com/"


def _resolve_self_hosted(url: str) -> Path | None:
    """If the manifest URL is hosted on this site, return the local path.

    Without this, manifests served from /examples/ get fetched from the
    deployed site — which lags this branch by one CI/deploy cycle, so a
    PR that updates examples/*.json would build with the still-deployed
    (stale) author/etc. and lose the change. Self-hosted URLs MUST
    resolve to the local file so the build is a function of the working
    tree.
    """
    if not url.startswith(SELF_ORIGIN):
        return None
    rel = url[len(SELF_ORIGIN):].lstrip("/")
    candidate = SITE_ROOT / rel
    return candidate if candidate.is_file() else None


def _load_manifest(entry: dict) -> tuple[dict, str]:
    """Return (manifest_json, source_label).

    Resolution order:
      1. local file (if manifest_url is self-hosted on this site)
      2. live fetch
      3. on-disk cache fallback
    """
    eid = entry["id"]
    cache_path = CACHE_DIR / f"{eid}.json"
    url = entry["manifest_url"]

    local = _resolve_self_hosted(url)
    if local is not None:
        return json.loads(local.read_text(encoding="utf-8")), "local"

    try:
        raw = _fetch(url)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(raw)
        return json.loads(raw.decode("utf-8")), "live"
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        if cache_path.is_file():
            print(f"  warn  fetch failed for {eid} ({e}); using cache",
                  file=sys.stderr)
            return json.loads(cache_path.read_text(encoding="utf-8")), "cache"
        raise SystemExit(
            f"FATAL: could not fetch {url} and no cache at {cache_path}: {e}"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _e(s) -> str:
    """Escape arbitrary text into HTML-safe string."""
    if s is None:
        return ""
    return html.escape(str(s), quote=True)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", s.lower()).strip("-") or "x"


def _is_agent_author(author: dict | None) -> bool:
    """Heuristic: an author whose name mentions 'agent' or 'on behalf of'
    is itself an agent. Useful for the marketplace surface — agent-authored
    tools are a feature."""
    if not isinstance(author, dict):
        return False
    name = (author.get("name") or "").lower()
    return any(k in name for k in (
        "agent", "on behalf of", "raven of", "operating on behalf",
    ))


def _initials(s: str, n: int = 2) -> str:
    parts = [p for p in re.split(r"[\s\-_]+", s) if p]
    if not parts:
        return "?"
    return "".join(p[0] for p in parts[:n]).upper()


# ---------------------------------------------------------------------------
# Shared chrome
# ---------------------------------------------------------------------------

NAV_HTML = """\
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
      <a href="/registry/">registry</a>
      <a href="/changelog/">changelog</a>
      <a href="https://github.com/drknowhow/install-manifest-spec">spec</a>
      <a href="https://yepgent.com/">yepgent</a>
    </nav>
  </header>
"""


def _footer(generated_at: str) -> str:
    return f"""\
    <footer>
      <p>
        toolspace.yepgent.com &middot;
        <a href="/registry/">registry</a> &middot;
        <a href="/changelog/">changelog</a> &middot;
        <a href="https://github.com/drknowhow/toolspace-site">source</a> &middot;
        <a href="https://github.com/drknowhow/install-manifest-spec">spec</a> &middot;
        <a href="https://yepgent.com">yepgent</a>
      </p>
      <p class="small muted">Pages generated {_e(generated_at)} &middot; <a href="/manifests.json">manifests.json</a> &middot; <a href="/changelog.json">changelog.json</a></p>
    </footer>
"""


# Inline CSS shared by registry pages. Inline (not in style.css) to keep
# the marketplace surface evolveable without tax on the rest of the site.
REGISTRY_CSS = """\
    /* Marketplace surface — used on /registry/ and /registry/<id>/. */

    main.wide { max-width: 56rem; }

    .filter-bar {
      display: flex;
      flex-direction: column;
      gap: 0.55rem;
      margin: 1rem 0 1.25rem;
      padding: 0.7rem 0.85rem;
      background: var(--card-bg);
      border: 1px solid var(--card-edge);
      border-radius: 10px;
    }
    .filter-row {
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem 0.8rem;
      align-items: center;
    }
    .filter-group { display: flex; flex-wrap: wrap; gap: 0.35rem; align-items: center; }
    .filter-group .label {
      font-size: 0.78rem;
      color: var(--muted);
      letter-spacing: 0.04em;
      text-transform: uppercase;
      margin-right: 0.3rem;
    }
    .filter-search {
      flex: 1 1 auto;
      min-width: 0;
      padding: 0.45em 0.75em;
      font: inherit;
      font-size: 0.9rem;
      color: var(--fg);
      background: transparent;
      border: 1px solid var(--field-edge);
      border-radius: 8px;
      outline: none;
      transition: border-color 120ms ease;
    }
    .filter-search:focus { border-color: var(--accent); }
    .filter-search::placeholder { color: var(--muted); }
    .filter-count {
      font-size: 0.78rem;
      color: var(--muted);
      white-space: nowrap;
      letter-spacing: 0.02em;
    }
    /* Capability disclosure — collapses the long tag list by default. */
    details.filter-disclosure { width: 100%; }
    details.filter-disclosure > summary {
      list-style: none;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      font-size: 0.78rem;
      color: var(--muted);
      letter-spacing: 0.04em;
      text-transform: uppercase;
      padding: 0.15em 0;
    }
    details.filter-disclosure > summary::-webkit-details-marker { display: none; }
    details.filter-disclosure > summary::before {
      content: "▸";
      font-size: 0.7rem;
      color: var(--muted);
      transition: transform 120ms ease;
    }
    details.filter-disclosure[open] > summary::before { content: "▾"; }
    details.filter-disclosure > summary:hover { color: var(--fg); }
    details.filter-disclosure > summary .badge-count {
      font-size: 0.72rem;
      color: var(--muted);
      text-transform: none;
      letter-spacing: 0;
    }
    details.filter-disclosure > summary .active-hint {
      font-size: 0.72rem;
      color: var(--accent);
      text-transform: none;
      letter-spacing: 0;
    }
    details.filter-disclosure .chip-row {
      display: flex;
      flex-wrap: wrap;
      gap: 0.3rem 0.35rem;
      margin-top: 0.5rem;
      padding-top: 0.5rem;
      border-top: 1px solid var(--rule);
    }
    .chip {
      display: inline-block;
      font: inherit;
      font-size: 0.78rem;
      font-weight: 500;
      letter-spacing: 0.02em;
      padding: 0.25em 0.7em;
      border-radius: 999px;
      cursor: pointer;
      color: var(--muted);
      background: transparent;
      border: 1px solid var(--field-edge);
      transition: color 120ms ease, border-color 120ms ease, background 120ms ease;
      user-select: none;
    }
    .chip:hover { color: var(--fg); border-color: var(--accent-edge); }
    .chip[aria-pressed="true"] {
      color: var(--accent);
      border-color: var(--accent);
      background: var(--accent-soft);
    }
    .chip-clear {
      font-size: 0.78rem;
      color: var(--muted);
      background: transparent;
      border: none;
      cursor: pointer;
      text-decoration: underline;
      text-decoration-color: color-mix(in oklab, var(--muted) 40%, transparent);
      padding: 0.2em 0.4em;
    }
    .chip-clear:hover { color: var(--fg); }

    /* Card grid */
    ul.tool-grid {
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      gap: 0.9rem;
      grid-template-columns: 1fr;
    }
    @media (min-width: 38rem) {
      ul.tool-grid { grid-template-columns: 1fr 1fr; }
    }
    @media (min-width: 60rem) {
      ul.tool-grid { grid-template-columns: 1fr 1fr 1fr; }
    }
    li.tool-card {
      background: var(--card-bg);
      border: 1px solid var(--card-edge);
      border-radius: 12px;
      padding: 1rem 1.1rem;
      display: flex;
      flex-direction: column;
      gap: 0.55rem;
      transition: border-color 0.15s ease, transform 0.15s ease;
    }
    li.tool-card:hover { border-color: var(--accent-edge); }
    li.tool-card.hidden { display: none; }
    .tool-card .head {
      display: flex;
      align-items: center;
      gap: 0.7rem;
    }
    .tool-glyph {
      flex: 0 0 auto;
      width: 40px;
      height: 40px;
      border-radius: 9px;
      background: var(--accent-soft);
      border: 1px solid var(--accent-edge);
      color: var(--accent);
      display: grid;
      place-items: center;
      font: 600 0.92rem ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      letter-spacing: 0.04em;
    }
    .tool-card h3 {
      margin: 0;
      font-size: 1.02rem;
      font-weight: 600;
      line-height: 1.25;
    }
    .tool-card h3 a { color: var(--fg); text-decoration: none; }
    .tool-card h3 a:hover { color: var(--accent); }
    .tool-card .author {
      font-size: 0.82rem;
      color: var(--muted);
      margin: 0;
    }
    .tool-card .summary {
      font-size: 0.92rem;
      color: var(--muted);
      margin: 0;
      line-height: 1.5;
    }
    .tool-card .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 0.3rem 0.4rem;
      align-items: center;
      margin-top: auto;
      padding-top: 0.3rem;
    }
    /* Cap-badge density limit: show first 3 by default, "+N more"
       toggle exposes the rest. Keeps cards uniform at a glance. */
    .tool-card .meta .badge.cap-overflow { display: none; }
    .tool-card.show-all-caps .meta .badge.cap-overflow { display: inline-block; }
    .badge.cap-more {
      cursor: pointer;
      background: transparent;
      border-style: dashed;
      color: var(--muted);
    }
    .badge.cap-more:hover { color: var(--fg); border-color: var(--accent-edge); }
    .badge {
      display: inline-block;
      font-size: 0.7rem;
      font-weight: 600;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      padding: 0.18em 0.55em;
      border-radius: 999px;
      border: 1px solid var(--field-edge);
      color: var(--muted);
    }
    .badge.cap {
      color: var(--fg);
      background: color-mix(in oklab, var(--fg) 6%, transparent);
      border-color: var(--card-edge);
      text-transform: none;
      letter-spacing: 0;
      font-weight: 500;
      font-size: 0.74rem;
    }
    .badge.status-stable     { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
    .badge.status-example    { color: var(--muted); }
    .badge.status-preview    { color: #f0b85a; border-color: #f0b85a; background: rgba(240, 184, 90, 0.10); }
    .badge.status-deprecated { color: #ff8585; border-color: #ff8585; background: rgba(255, 133, 133, 0.08); }
    .badge.agent-author {
      color: var(--accent);
      border-color: var(--accent-edge);
      background: var(--accent-soft);
    }

    /* Empty state */
    .grid-empty {
      text-align: center;
      padding: 2.5rem 1rem;
      color: var(--muted);
      border: 1px dashed var(--card-edge);
      border-radius: 10px;
    }

    /* ----- Product page (per-manifest) ----- */

    .crumb { font-size: 0.85rem; color: var(--muted); margin: 1rem 0 0.5rem; }
    .crumb a { color: var(--muted); text-decoration: underline; text-decoration-color: color-mix(in oklab, var(--muted) 40%, transparent); }
    .crumb a:hover { color: var(--fg); }

    .hero-block {
      display: flex;
      gap: 1rem;
      align-items: flex-start;
      margin: 0 0 1rem;
    }
    .hero-block .tool-glyph { width: 56px; height: 56px; font-size: 1.15rem; border-radius: 11px; }
    .hero-block .meta-line {
      display: flex; flex-wrap: wrap; gap: 0.4rem;
      margin: 0.3rem 0 0;
    }
    .hero-block h1 {
      margin: 0;
      font-size: clamp(1.6rem, 4.4vw, 2.25rem);
      letter-spacing: -0.015em;
      line-height: 1.18;
    }
    .author-line {
      margin: 0.35rem 0 0;
      color: var(--muted);
      font-size: 0.93rem;
    }
    .summary-line {
      margin: 0.6rem 0 0.4rem;
      font-size: 1.02rem;
      line-height: 1.55;
    }

    .install-card {
      background: var(--card-bg);
      border: 1px solid var(--accent-edge);
      border-radius: 10px;
      padding: 0.85rem 1rem;
      margin: 1rem 0 1.2rem;
    }
    .install-card .label {
      font-size: 0.78rem;
      font-weight: 600;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--accent);
      margin: 0 0 0.4rem;
    }
    .install-card pre { margin: 0; }
    .install-card .links { margin: 0.5rem 0 0; font-size: 0.85rem; }
    .install-card .links a { margin-right: 0.7rem; }

    section.panel {
      margin: 1.6rem 0;
      padding: 1rem 1.1rem;
      background: var(--card-bg);
      border: 1px solid var(--card-edge);
      border-radius: 11px;
    }
    section.panel.security { border-color: var(--accent-edge); }
    section.panel h2 {
      margin: 0 0 0.6rem;
      font-size: 1rem;
      font-weight: 600;
      display: flex;
      align-items: center;
      gap: 0.45rem;
    }
    section.panel h2 .glyph {
      width: 16px; height: 16px;
      color: var(--muted);
    }
    section.panel.security h2 .glyph { color: var(--accent); }

    table.kv {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.92rem;
      margin: 0.4rem 0;
    }
    table.kv th, table.kv td {
      text-align: left;
      vertical-align: top;
      padding: 0.45em 0.5em 0.45em 0;
      border-bottom: 1px solid var(--rule);
    }
    table.kv th {
      color: var(--muted);
      font-weight: 500;
      width: 12rem;
      white-space: nowrap;
    }
    table.kv td p { margin: 0; }
    table.kv tr:last-child th, table.kv tr:last-child td { border-bottom: none; }

    ul.scope-list, ul.action-list, ul.transmit-list, ul.reads-list {
      list-style: none;
      padding: 0;
      margin: 0;
    }
    ul.scope-list li, ul.action-list li, ul.transmit-list li, ul.reads-list li {
      padding: 0.6em 0;
      border-bottom: 1px solid var(--rule);
    }
    ul.scope-list li:last-child, ul.action-list li:last-child,
    ul.transmit-list li:last-child, ul.reads-list li:last-child { border-bottom: none; }
    .scope-head, .action-head, .transmit-head {
      display: flex; flex-wrap: wrap; gap: 0.4rem; align-items: baseline;
      margin-bottom: 0.2em;
    }
    .scope-head code { font-size: 0.92em; }
    .scope-actions { color: var(--muted); font-size: 0.85rem; }
    .scope-rationale, .action-summary, .transmit-purpose {
      color: var(--muted); font-size: 0.92rem; margin: 0.2rem 0 0; line-height: 1.55;
    }

    .action-name { font-weight: 600; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.95rem; color: var(--fg); }
    .action-side {
      font-size: 0.7rem;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .action-side.write, .action-side.destructive { color: #f0b85a; }
    .action-docs { margin: 0.4em 0 0; }
    .action-docs dt {
      font-size: 0.78rem;
      color: var(--muted);
      letter-spacing: 0.04em;
      text-transform: uppercase;
      margin-top: 0.4em;
    }
    .action-docs dd {
      margin: 0.1em 0 0;
      font-size: 0.9rem;
      line-height: 1.55;
    }
    .action-docs dd code { font-size: 0.86em; }

    .transmit-target {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-weight: 600;
      color: var(--fg);
    }
    .transmit-fields {
      margin: 0.35rem 0 0;
      padding-left: 1.1rem;
      font-size: 0.86rem;
      color: var(--muted);
    }
    .retention {
      font-size: 0.74rem;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      padding: 0.18em 0.55em;
      border-radius: 999px;
      border: 1px solid var(--field-edge);
      color: var(--muted);
    }
    .retention.persistent { color: #f0b85a; border-color: #f0b85a; background: rgba(240, 184, 90, 0.08); }
    .retention.session    { color: var(--muted); }
    .retention.none       { color: var(--accent); border-color: var(--accent-edge); background: var(--accent-soft); }

    .smoke-block, .kill-block { font-size: 0.92rem; }
    .smoke-block code, .kill-block code { font-size: 0.88em; }
    .kill-block .url-target { word-break: break-all; }

    .freshness {
      margin-top: 1.5rem;
      font-size: 0.78rem;
      color: var(--muted);
    }
    .freshness .stale { color: #f0b85a; }
"""


# ---------------------------------------------------------------------------
# Render: registry index (card grid)
# ---------------------------------------------------------------------------

def _render_card(entry: dict, manifest: dict) -> str:
    eid = entry["id"]
    tool = manifest.get("tool", {}) or {}
    name = tool.get("name") or entry.get("name") or eid
    summary = tool.get("summary") or entry.get("description") or ""
    if len(summary) > 220:
        summary = summary[:217].rstrip() + "…"
    author = tool.get("author") or {}
    author_name = author.get("name") or "—"
    if len(author_name) > 90:
        author_name = author_name[:87] + "…"
    is_agent = _is_agent_author(author)
    caps = entry.get("capabilities") or []
    status = entry.get("status") or "example"
    mv = entry.get("manifest_version") or manifest.get("manifest_version") or "0.3"
    glyph = _initials(name)

    # data-* attributes drive the client-side filter.
    data_caps = " ".join(_slug(c) for c in caps)
    data_status = _slug(status)
    data_mv = f"v{mv}"

    # Cap-badge density: first 3 always shown, rest tagged overflow.
    CAP_VISIBLE = 3
    cap_pills_parts = []
    for i, c in enumerate(caps):
        cls = "badge cap" if i < CAP_VISIBLE else "badge cap cap-overflow"
        cap_pills_parts.append(f'<span class="{cls}">{_e(c)}</span>')
    overflow_n = max(0, len(caps) - CAP_VISIBLE)
    if overflow_n:
        cap_pills_parts.append(
            f'<button type="button" class="badge cap-more" '
            f'data-cap-more aria-expanded="false">'
            f'+{overflow_n} more</button>'
        )
    cap_pills = "".join(cap_pills_parts)
    status_pill = (
        f'<span class="badge status-{_e(_slug(status))}">{_e(status)}</span>'
    )
    mv_pill = f'<span class="badge">v{_e(mv)}</span>'
    agent_pill = (
        '<span class="badge agent-author" title="Authored by an agent">agent author</span>'
        if is_agent else ""
    )

    # data-search: lowercased blob used by the text-search filter.
    # Includes name, author, summary, and capability tags.
    search_blob = " ".join([name, author_name, summary, " ".join(caps)]).lower()

    return f"""\
        <li class="tool-card" data-caps="{_e(data_caps)}" data-status="{_e(data_status)}" data-mv="{_e(data_mv)}" data-search="{_e(search_blob)}">
          <div class="head">
            <div class="tool-glyph" aria-hidden="true">{_e(glyph)}</div>
            <div>
              <h3><a href="/registry/{_e(eid)}/">{_e(name)}</a></h3>
              <p class="author">by {_e(author_name)}</p>
            </div>
          </div>
          <p class="summary">{_e(summary)}</p>
          <div class="meta">
            {status_pill}
            {mv_pill}
            {agent_pill}
            {cap_pills}
          </div>
        </li>
"""


def _collect_filters(entries_with_manifests: list[tuple[dict, dict]]) -> dict:
    caps: set[str] = set()
    statuses: set[str] = set()
    mvs: set[str] = set()
    for entry, manifest in entries_with_manifests:
        for c in entry.get("capabilities") or []:
            caps.add(c)
        statuses.add(entry.get("status") or "example")
        mvs.add(f"v{entry.get('manifest_version') or manifest.get('manifest_version') or '0.3'}")
    return {
        "capabilities": sorted(caps),
        "statuses": sorted(statuses),
        "manifest_versions": sorted(mvs),
    }


REGISTRY_INDEX_FILTER_JS = """\
  <script>
  (function () {
    var grid = document.getElementById('tool-grid');
    if (!grid) return;
    var cards = grid.querySelectorAll('li.tool-card');
    var emptyState = document.getElementById('grid-empty');
    var clearBtn = document.getElementById('chip-clear');
    var searchInput = document.getElementById('registry-search');
    var countEl = document.getElementById('filter-count');
    var totalCards = cards.length;

    function activeFor(group) {
      var out = [];
      document.querySelectorAll('[data-filter-group="' + group + '"][aria-pressed="true"]').forEach(function (b) {
        out.push(b.getAttribute('data-value'));
      });
      return out;
    }

    function syncDisclosureHints() {
      // Show a small "(N selected)" hint on collapsed disclosures so users
      // notice active filters without opening the panel.
      document.querySelectorAll('details.filter-disclosure[data-disclosure-group]').forEach(function (det) {
        var group = det.getAttribute('data-disclosure-group');
        var active = activeFor(group);
        var hint = det.querySelector('[data-active-hint]');
        if (!hint) return;
        if (active.length > 0) {
          hint.textContent = '· ' + active.length + ' selected';
          hint.hidden = false;
        } else {
          hint.textContent = '';
          hint.hidden = true;
        }
      });
    }

    function apply() {
      var caps = activeFor('cap');
      var statuses = activeFor('status');
      var mvs = activeFor('mv');
      var q = (searchInput && searchInput.value || '').trim().toLowerCase();
      var visible = 0;
      cards.forEach(function (card) {
        var cardCaps = (card.getAttribute('data-caps') || '').split(/\\s+/).filter(Boolean);
        var cardStatus = card.getAttribute('data-status') || '';
        var cardMv = card.getAttribute('data-mv') || '';
        var cardSearch = card.getAttribute('data-search') || '';
        var pass = true;
        if (caps.length && !caps.every(function (c) { return cardCaps.indexOf(c) >= 0; })) pass = false;
        if (statuses.length && statuses.indexOf(cardStatus) < 0) pass = false;
        if (mvs.length && mvs.indexOf(cardMv) < 0) pass = false;
        if (q && cardSearch.indexOf(q) < 0) pass = false;
        if (pass) {
          card.classList.remove('hidden');
          visible++;
        } else {
          card.classList.add('hidden');
        }
      });
      if (emptyState) emptyState.style.display = visible === 0 ? 'block' : 'none';
      if (countEl) countEl.textContent = visible + ' of ' + totalCards;
      syncDisclosureHints();
    }

    document.querySelectorAll('button.chip[data-filter-group]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var pressed = btn.getAttribute('aria-pressed') === 'true';
        btn.setAttribute('aria-pressed', pressed ? 'false' : 'true');
        apply();
      });
    });
    if (searchInput) {
      searchInput.addEventListener('input', apply);
    }
    if (clearBtn) {
      clearBtn.addEventListener('click', function () {
        document.querySelectorAll('button.chip[data-filter-group]').forEach(function (b) {
          b.setAttribute('aria-pressed', 'false');
        });
        if (searchInput) searchInput.value = '';
        apply();
      });
    }

    // Per-card "+N more" expander for capability badges.
    grid.querySelectorAll('button.cap-more[data-cap-more]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var card = btn.closest('.tool-card');
        if (!card) return;
        var expanded = card.classList.toggle('show-all-caps');
        btn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        btn.textContent = expanded ? 'show less' : btn.dataset.label || btn.textContent;
        if (expanded && !btn.dataset.label) {
          // Stash original label on first expand so we can restore it.
          // We didn't store it server-side; reconstruct from overflow count.
          var overflow = card.querySelectorAll('.badge.cap-overflow').length;
          btn.dataset.label = '+' + overflow + ' more';
        }
      });
    });
  })();
  </script>
"""


def render_registry_index(
    entries_with_manifests: list[tuple[dict, dict]],
    generated_at: str,
) -> str:
    filters = _collect_filters(entries_with_manifests)

    def _chip_group(label: str, group: str, values: list[str]) -> str:
        chips = "".join(
            f'<button class="chip" data-filter-group="{_e(group)}" data-value="{_e(_slug(v))}" aria-pressed="false">{_e(v)}</button>'
            for v in values
        )
        return f"""\
        <div class="filter-group">
          <span class="label">{_e(label)}</span>
          {chips}
        </div>
"""

    def _chip_disclosure(label: str, group: str, values: list[str]) -> str:
        """Collapsed-by-default chip group — used for long lists (capabilities)."""
        chips = "".join(
            f'<button class="chip" data-filter-group="{_e(group)}" data-value="{_e(_slug(v))}" aria-pressed="false">{_e(v)}</button>'
            for v in values
        )
        return f"""\
      <details class="filter-disclosure" data-disclosure-group="{_e(group)}">
        <summary>
          <span class="label">{_e(label)}</span>
          <span class="badge-count">({len(values)})</span>
          <span class="active-hint" data-active-hint hidden></span>
        </summary>
        <div class="chip-row">{chips}</div>
      </details>
"""

    cards = "".join(
        _render_card(entry, manifest)
        for entry, manifest in entries_with_manifests
    )

    n_entries = len(entries_with_manifests)

    # Status filter is only useful when there's more than one distinct value.
    # Today everything is "example" — surfacing a single-value group is noise.
    show_status_filter = len(filters['statuses']) > 1
    status_group_html = _chip_group('Status', 'status', filters['statuses']) if show_status_filter else ""

    body = f"""\
    <p class="crumb"><a href="/">toolspace</a> &rsaquo; registry</p>
    <h1>Registry</h1>
    <p class="lede">Install manifests for autonomous agents. {n_entries} entr{'y' if n_entries == 1 else 'ies'}, hand-curated.</p>

    <div class="filter-bar" role="region" aria-label="Filter manifests">
      <div class="filter-row">
        <input
          id="registry-search"
          class="filter-search"
          type="search"
          autocomplete="off"
          spellcheck="false"
          placeholder="Search by name, author, summary, tag…"
          aria-label="Search manifests"
        />
        <span class="filter-count" id="filter-count">{n_entries} of {n_entries}</span>
        <button id="chip-clear" class="chip-clear" type="button">clear</button>
      </div>
      <div class="filter-row">
{_chip_group('Schema', 'mv', filters['manifest_versions'])}\
{status_group_html}\
      </div>
{_chip_disclosure('Capability', 'cap', filters['capabilities'])}\
    </div>

    <ul class="tool-grid" id="tool-grid">
{cards}    </ul>

    <div id="grid-empty" class="grid-empty" style="display:none;">
      No manifests match the selected filters.
    </div>

    <p class="small muted" style="margin-top:1.5rem;">
      Want yours listed? Open a PR on
      <a href="https://github.com/drknowhow/toolspace-site">toolspace-site</a>
      adding an entry to <a href="/manifests.json"><code>manifests.json</code></a>.
      Manifest must validate against the
      <a href="/schemas/install-manifest-v0.3.json"><code>v0.3 schema</code></a>.
    </p>
"""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>registry &middot; toolspace</title>
  <meta name="description" content="Browse install manifests for autonomous agents — capability, status, schema-version filters." />
  <meta name="color-scheme" content="dark light" />

  <link rel="alternate" type="application/json" href="/manifests.json" title="toolspace manifest index" />

  <link rel="stylesheet" href="/style.css" />
  <style>
{REGISTRY_CSS}\
  </style>
</head>
<body>
{NAV_HTML}\
  <main class="wide">
{body}
{_footer(generated_at)}\
  </main>

{REGISTRY_INDEX_FILTER_JS}\
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Render: per-manifest detail (product page)
# ---------------------------------------------------------------------------

def _retention_class(t: str) -> str:
    t = (t or "").lower()
    if "persistent" in t or "indefinite" in t:
        return "persistent"
    if "session" in t or "ephemeral" in t:
        return "session"
    return "none"


def _render_security_panel(manifest: dict) -> str:
    parts: list[str] = []

    # kill_switch
    ks = manifest.get("kill_switch") or {}
    ks_kind = ks.get("kind", "—")
    if ks_kind == "url":
        ks_body = (
            f'<p class="kill-block">Manual: revoke at '
            f'<a href="{_e(ks.get("url", ""))}" rel="noopener noreferrer">'
            f'<code class="url-target">{_e(ks.get("url", ""))}</code></a>.</p>'
        )
    elif ks_kind == "shell":
        ks_body = (
            f'<p class="kill-block">Programmatic: '
            f'<code>{_e(ks.get("command", ""))}</code></p>'
        )
    elif ks_kind == "manual":
        ks_body = (
            f'<p class="kill-block">Manual procedure: '
            f'{_e(ks.get("instructions", "—"))}</p>'
        )
    else:
        ks_body = f'<p class="kill-block">{_e(json.dumps(ks))}</p>'

    parts.append(f"""\
      <h2>
        <svg class="glyph" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M12 4l8 4v6a8 8 0 01-8 6 8 8 0 01-8-6V8z"/>
        </svg>
        Security
      </h2>
      <table class="kv">
        <tr><th>Kill switch</th><td><span class="badge">{_e(ks_kind)}</span> {ks_body}</td></tr>
""")

    # smoke
    smoke = manifest.get("smoke") or {}
    smoke_kind = smoke.get("kind", "—")
    smoke_summary_bits = []
    if smoke_kind == "action-call":
        smoke_summary_bits.append(f'action <code>{_e(smoke.get("action", "—"))}</code>')
    if "timeout_seconds" in smoke:
        smoke_summary_bits.append(f'timeout {_e(smoke["timeout_seconds"])}s')
    success = smoke.get("success") or {}
    if success.get("json_pointer_equals"):
        for ptr, expected in success["json_pointer_equals"].items():
            smoke_summary_bits.append(
                f'<code>{_e(ptr)}</code> = <code>{_e(expected)}</code>'
            )

    smoke_body = (
        f'<p class="smoke-block"><span class="badge">{_e(smoke_kind)}</span> '
        + " &middot; ".join(smoke_summary_bits or ["—"])
        + "</p>"
    )
    parts.append(f"        <tr><th>Smoke contract</th><td>{smoke_body}</td></tr>\n")

    parts.append("      </table>\n")

    # scopes
    scopes = manifest.get("scopes") or []
    if scopes:
        items = []
        for s in scopes:
            actions_join = ", ".join(s.get("actions") or [])
            ps = s.get("provider_scope")
            ps_html = (
                f'<span class="badge cap" title="provider scope">{_e(ps)}</span>'
                if ps else ""
            )
            items.append(f"""\
        <li>
          <div class="scope-head">
            <code>{_e(s.get("resource", "—"))}</code>
            <span class="scope-actions">{_e(actions_join)}</span>
            {ps_html}
          </div>
          <p class="scope-rationale">{_e(s.get("rationale", ""))}</p>
        </li>
""")
        parts.append(f"""\
      <h3 style="margin-top:1.2rem;">Scopes</h3>
      <ul class="scope-list">
{"".join(items)}\
      </ul>
""")

    # data_boundary
    db = manifest.get("data_boundary") or {}
    if db:
        sub_parts: list[str] = []

        reads = db.get("reads") or []
        if reads:
            read_items = "".join(
                f'<li><code>{_e(r.get("resource", "—"))}</code> '
                f'<span class="scope-actions">sensitivity: {_e(r.get("sensitivity", "—"))}</span></li>'
                for r in reads
            )
            sub_parts.append(
                f'<h4 style="margin-top:0.8rem;font-size:0.86rem;color:var(--muted);'
                f'text-transform:uppercase;letter-spacing:0.05em;">Reads</h4>'
                f'<ul class="reads-list">{read_items}</ul>'
            )

        transmits = db.get("transmits") or []
        if transmits:
            t_items = []
            for t in transmits:
                rt = (t.get("third_party_retention") or "").strip()
                rt_class = _retention_class(rt)
                fields = t.get("fields") or []
                fields_html = (
                    "<ul class=\"transmit-fields\">"
                    + "".join(f"<li><code>{_e(f)}</code></li>" for f in fields)
                    + "</ul>"
                ) if fields else ""
                t_items.append(f"""\
        <li>
          <div class="transmit-head">
            <span class="transmit-target">{_e(t.get("to", "—"))}</span>
            <span class="retention {_e(rt_class)}" title="third-party retention">{_e(rt or "—")}</span>
          </div>
          <p class="transmit-purpose">{_e(t.get("purpose", ""))}</p>
          {fields_html}
        </li>
""")
            sub_parts.append(
                f'<h4 style="margin-top:0.8rem;font-size:0.86rem;color:var(--muted);'
                f'text-transform:uppercase;letter-spacing:0.05em;">Transmits</h4>'
                f'<ul class="transmit-list">{"".join(t_items)}</ul>'
            )

        persists = db.get("persists") or []
        retention = db.get("retention") or {}
        if persists or retention:
            persist_html = ""
            if persists:
                persist_html = "<ul class=\"reads-list\">" + "".join(
                    f'<li><code>{_e(p.get("resource", "—"))}</code></li>'
                    for p in persists
                ) + "</ul>"
            else:
                persist_html = '<p class="small muted">Nothing persisted by the tool itself.</p>'
            ret_html = ""
            if retention:
                ret_html = (
                    f'<p class="small muted">Tool-local retention: '
                    f'{_e(retention.get("tool_local_days", "—"))} day(s).</p>'
                )
            sub_parts.append(
                f'<h4 style="margin-top:0.8rem;font-size:0.86rem;color:var(--muted);'
                f'text-transform:uppercase;letter-spacing:0.05em;">Persists</h4>'
                f'{persist_html}{ret_html}'
            )

        if sub_parts:
            parts.append(
                '<h3 style="margin-top:1.2rem;">Data boundary</h3>'
                + "".join(sub_parts)
            )

    return f'<section class="panel security">\n' + "".join(parts) + "</section>\n"


def _render_actions(manifest: dict) -> str:
    actions = manifest.get("actions") or []
    if not actions:
        return ""
    items = []
    for a in actions:
        docs = a.get("docs") or {}
        side = a.get("side_effects") or "read"
        idem = a.get("idempotent")
        scopes_used = a.get("scopes_used") or []
        scopes_html = ""
        if scopes_used:
            scopes_html = " &middot; " + " ".join(
                f'<code>{_e(s)}</code>' for s in scopes_used
            )
        idem_html = (
            ' <span class="action-side">idempotent</span>'
            if idem else ""
        )
        items.append(f"""\
        <li>
          <div class="action-head">
            <span class="action-name">{_e(a.get("name", "—"))}</span>
            <span class="action-side {_e(_slug(side))}">{_e(side)}</span>
            {idem_html}
          </div>
          <p class="action-summary">{_e(a.get("summary", ""))}{scopes_html}</p>
          <dl class="action-docs">
            <dt>Goal</dt>            <dd>{_e(docs.get("goal", "—"))}</dd>
            <dt>Inputs</dt>          <dd><code>{_e(docs.get("inputs_brief", "—"))}</code></dd>
            <dt>Outputs</dt>         <dd><code>{_e(docs.get("outputs_brief", "—"))}</code></dd>
            <dt>Errors</dt>          <dd><code>{_e(docs.get("errors_brief", "—"))}</code></dd>
            <dt>Example</dt>         <dd><code>{_e(docs.get("example", "—"))}</code></dd>
          </dl>
        </li>
""")
    return f"""\
    <section class="panel">
      <h2>
        <svg class="glyph" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <rect x="3" y="4" width="18" height="16" rx="2"/>
          <path d="M7 9l3 3-3 3"/>
          <line x1="13" y1="15" x2="17" y2="15"/>
        </svg>
        Actions
      </h2>
      <ul class="action-list">
{"".join(items)}\
      </ul>
    </section>
"""


def _render_env(manifest: dict) -> str:
    envs = manifest.get("env") or []
    if not envs:
        return ""
    rows = []
    for e in envs:
        secret = "yes" if e.get("secret") else "no"
        required = "required" if e.get("required") else "optional"
        obtain = e.get("obtain_url")
        obtain_html = (
            f' &middot; <a href="{_e(obtain)}" rel="noopener noreferrer">obtain</a>'
            if obtain else ""
        )
        default = e.get("default")
        default_html = (
            f' <span class="small muted">(default <code>{_e(default)}</code>)</span>'
            if default is not None else ""
        )
        rows.append(f"""\
        <tr>
          <th><code>{_e(e.get("name", "—"))}</code></th>
          <td>
            <p>{_e(e.get("prompt", ""))}</p>
            <p class="small muted">{_e(required)} &middot; secret: {_e(secret)}{default_html}{obtain_html}</p>
          </td>
        </tr>
""")
    return f"""\
    <section class="panel">
      <h2>
        <svg class="glyph" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <rect x="4" y="6" width="16" height="12" rx="2"/>
          <line x1="8" y1="10" x2="16" y2="10"/>
          <line x1="8" y1="14" x2="13" y2="14"/>
        </svg>
        Environment
      </h2>
      <table class="kv">
{"".join(rows)}\
      </table>
    </section>
"""


def _render_verify_and_cost(manifest: dict) -> str:
    verify = manifest.get("verify") or {}
    cost = manifest.get("cost") or {}
    rows: list[str] = []

    if verify:
        sla = verify.get("sla") or {}
        sched = verify.get("schedule") or {}
        suite = verify.get("suite") or {}
        rows.append(f"""\
        <tr><th>Verify suite</th><td><code>{_e(suite.get("ref", "—"))}</code> &middot; {_e(suite.get("case_count", "—"))} cases &middot; pass &ge; {_e(suite.get("pass_threshold", "—"))}</td></tr>
""")
        if sla:
            rows.append(f"""\
        <tr><th>SLA</th><td>p50 {_e(sla.get("p50_latency_ms", "—"))} ms &middot; p95 {_e(sla.get("p95_latency_ms", "—"))} ms &middot; max error rate {_e(sla.get("error_rate_max", "—"))}</td></tr>
""")
        if sched:
            rows.append(f"""\
        <tr><th>Schedule</th><td>cadence: {_e(sched.get("cadence", "—"))} &middot; on install: {_e("yes" if sched.get("on_install") else "no")}</td></tr>
""")

    if cost:
        rows.append(f"""\
        <tr><th>Install fee</th><td>{_e(cost.get("install_fee_cents", 0))}&cent;</td></tr>
        <tr><th>Monthly fee</th><td>{_e(cost.get("monthly_fee_cents", 0))}&cent;</td></tr>
        <tr><th>Usage model</th><td>{_e(cost.get("usage_model", "none"))}</td></tr>
""")

    if not rows:
        return ""
    return f"""\
    <section class="panel">
      <h2>
        <svg class="glyph" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M5 12l5 5 9-11"/>
        </svg>
        Verify &amp; cost
      </h2>
      <table class="kv">
{"".join(rows)}\
      </table>
    </section>
"""


def render_product_page(
    entry: dict,
    manifest: dict,
    fetched_at: str,
    source_label: str,
    generated_at: str,
) -> str:
    eid = entry["id"]
    tool = manifest.get("tool", {}) or {}
    name = tool.get("name") or entry.get("name") or eid
    summary = tool.get("summary") or entry.get("description") or ""
    description = tool.get("description") or ""
    tags = tool.get("tags") or []
    homepage = tool.get("homepage")
    license_str = tool.get("license") or "—"
    author = tool.get("author") or {}
    author_name = author.get("name") or "—"
    author_url = author.get("url")
    is_agent = _is_agent_author(author)

    caps = entry.get("capabilities") or []
    status = entry.get("status") or "example"
    mv = entry.get("manifest_version") or manifest.get("manifest_version") or "0.3"
    glyph = _initials(name)

    runtime = manifest.get("runtime", {}) or {}
    install = runtime.get("install", {}) or {}
    entrypoint = runtime.get("entrypoint", {}) or {}

    install_cmd = f"install-manifest install {entry['manifest_url']}"
    runtime_summary_bits = []
    runtime_kind = runtime.get("kind") or "—"
    runtime_summary_bits.append(f"runtime: <code>{_e(runtime_kind)}</code>")
    if install.get("method"):
        runtime_summary_bits.append(
            f"install method: <code>{_e(install['method'])}</code>"
        )
    if entrypoint.get("command"):
        runtime_summary_bits.append(
            f"entrypoint: <code>{_e(' '.join(entrypoint['command']))}</code>"
        )

    cap_pills = "".join(f'<span class="badge cap">{_e(c)}</span>' for c in caps)
    tag_pills = "".join(f'<span class="badge cap">{_e(t)}</span>' for t in tags)

    author_html = _e(author_name)
    if author_url:
        author_html = f'<a href="{_e(author_url)}" rel="noopener noreferrer">{author_html}</a>'
    if is_agent:
        author_html = f'{author_html} <span class="badge agent-author">agent author</span>'

    fresh_html = (
        f'<span class="freshness">Last fetched {_e(fetched_at)} '
        f'({_e(source_label)})</span>'
    )

    support = manifest.get("support") or {}
    support_links = []
    if support.get("docs_url"):
        support_links.append(f'<a href="{_e(support["docs_url"])}" rel="noopener noreferrer">docs</a>')
    if support.get("issues_url"):
        support_links.append(f'<a href="{_e(support["issues_url"])}" rel="noopener noreferrer">issues</a>')
    if homepage:
        support_links.append(f'<a href="{_e(homepage)}" rel="noopener noreferrer">homepage</a>')
    support_html = " &middot; ".join(support_links)

    body = f"""\
    <p class="crumb"><a href="/">toolspace</a> &rsaquo; <a href="/registry/">registry</a> &rsaquo; {_e(name)}</p>

    <div class="hero-block">
      <div class="tool-glyph" aria-hidden="true">{_e(glyph)}</div>
      <div>
        <h1>{_e(name)}</h1>
        <p class="author-line">by {author_html}</p>
        <div class="meta-line">
          <span class="badge status-{_e(_slug(status))}">{_e(status)}</span>
          <span class="badge">v{_e(mv)}</span>
          {cap_pills}
        </div>
      </div>
    </div>

    <p class="summary-line">{_e(summary)}</p>

    <section class="install-card">
      <p class="label">Install</p>
<pre><code>{_e(install_cmd)}</code></pre>
      <p class="links">
        <a href="{_e(entry["manifest_url"])}" rel="noopener noreferrer">manifest JSON</a>
        {('&middot; <a href="' + _e(entry.get("source", "")) + '" rel="noopener noreferrer">source</a>') if entry.get("source") else ""}
        {('&middot; ' + support_html) if support_html else ""}
      </p>
    </section>

{_render_security_panel(manifest)}\
{_render_actions(manifest)}\
{_render_env(manifest)}\
{_render_verify_and_cost(manifest)}\

    <section class="panel">
      <h2>
        <svg class="glyph" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="9"/>
          <path d="M12 7v5l3 3"/>
        </svg>
        Runtime
      </h2>
      <p class="small muted">{" &middot; ".join(runtime_summary_bits)}</p>
      {('<p>' + _e(description) + '</p>') if description else ""}
      {('<p class="small muted">Tags: ' + tag_pills + '</p>') if tag_pills else ""}
      <p class="small muted">License: {_e(license_str)}</p>
      <p>{fresh_html}</p>
    </section>
"""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_e(name)} &middot; toolspace</title>
  <meta name="description" content="{_e(summary[:160])}" />
  <meta name="color-scheme" content="dark light" />

  <link rel="alternate" type="application/json" href="{_e(entry["manifest_url"])}" title="install manifest JSON" />

  <link rel="stylesheet" href="/style.css" />
  <style>
{REGISTRY_CSS}\
  </style>
</head>
<body>
{NAV_HTML}\
  <main class="wide">
{body}
{_footer(generated_at)}\
  </main>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _now_utc_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:12]


def build(out_root: Path) -> int:
    if not INDEX_PATH.is_file():
        print(f"missing {INDEX_PATH}", file=sys.stderr)
        return 1
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    entries = index.get("manifests") or []

    # Note: --check uses the registry/ dir's content frozen at the build
    # time embedded in the page. Diffing pages that contain a wall-clock
    # timestamp would always drift, so the timestamp is read from the
    # existing committed registry/index.html when in --check, and is
    # "now" otherwise. Implemented in the caller.
    generated_at = (
        getattr(build, "_pinned_generated_at", None) or _now_utc_iso()
    )

    out_registry = out_root / "registry"
    out_registry.mkdir(parents=True, exist_ok=True)

    pinned_fetched = getattr(build, "_pinned_fetched", {}) or {}
    pinned_source = getattr(build, "_pinned_source", {}) or {}

    fetched: list[tuple[dict, dict]] = []
    for entry in entries:
        manifest, source_label = _load_manifest(entry)
        eid = entry["id"]
        if eid in pinned_fetched:
            fetched_at = pinned_fetched[eid]
            source_label = pinned_source.get(eid, source_label)
        elif source_label == "live":
            fetched_at = _now_utc_iso()
        elif source_label == "local":
            fetched_at = "(working tree)"
        else:
            fetched_at = "(cache)"
        page = render_product_page(
            entry, manifest, fetched_at, source_label, generated_at,
        )
        sub = out_registry / entry["id"]
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "index.html").write_text(page, encoding="utf-8")
        fetched.append((entry, manifest))
        print(f"  wrote registry/{entry['id']}/index.html")

    idx_html = render_registry_index(fetched, generated_at)
    (out_registry / "index.html").write_text(idx_html, encoding="utf-8")
    print(f"  wrote registry/index.html ({len(fetched)} cards)")

    return 0


def check() -> int:
    """Regenerate to a tempdir and diff against committed registry/."""
    if not REGISTRY_DIR.is_dir():
        print(f"missing committed dir: {REGISTRY_DIR}", file=sys.stderr)
        print("Run 'python scripts/build_registry_pages.py' locally and commit.",
              file=sys.stderr)
        return 1

    # Pin the "Pages generated" timestamp so the diff doesn't drift only
    # because of wall-clock. Read it back from the existing committed
    # index page.
    committed_index = REGISTRY_DIR / "index.html"
    if committed_index.is_file():
        text = committed_index.read_text(encoding="utf-8")
        m = re.search(r"Pages generated ([0-9T:Z\-]+)", text)
        if m:
            build._pinned_generated_at = m.group(1)  # type: ignore[attr-defined]

    # Pin per-product Last-fetched timestamps from committed pages, same
    # rationale: the wall-clock changes every run, but content drift is
    # what we actually care about.
    pinned_fetched: dict[str, str] = {}
    pinned_source: dict[str, str] = {}
    for sub in REGISTRY_DIR.iterdir():
        if not sub.is_dir():
            continue
        page = sub / "index.html"
        if not page.is_file():
            continue
        text = page.read_text(encoding="utf-8")
        m = re.search(
            r'Last fetched ([^<]+?) \(([^)]+)\)</span>', text,
        )
        if m:
            pinned_fetched[sub.name] = m.group(1)
            pinned_source[sub.name] = m.group(2)
    build._pinned_fetched = pinned_fetched  # type: ignore[attr-defined]
    build._pinned_source = pinned_source    # type: ignore[attr-defined]

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        rc = build(tdp)
        if rc != 0:
            return rc
        drift: list[str] = []
        # Compare every committed file against the rebuilt one.
        for committed in REGISTRY_DIR.rglob("*"):
            if committed.is_dir():
                continue
            rel = committed.relative_to(REGISTRY_DIR)
            rebuilt = tdp / "registry" / rel
            if not rebuilt.is_file():
                drift.append(f"DELETED {rel}")
                continue
            if committed.read_bytes() != rebuilt.read_bytes():
                drift.append(f"DRIFT {rel}")
        # Also catch new files that the build produced but aren't committed.
        for rebuilt in (tdp / "registry").rglob("*"):
            if rebuilt.is_dir():
                continue
            rel = rebuilt.relative_to(tdp / "registry")
            committed = REGISTRY_DIR / rel
            if not committed.is_file():
                drift.append(f"MISSING {rel}")

        if drift:
            for d in drift:
                print(f"  {d}", file=sys.stderr)
            print(
                f"\nERROR: {len(drift)} file(s) drift from a fresh build.\n"
                "Run 'python scripts/build_registry_pages.py' locally and commit.",
                file=sys.stderr,
            )
            return 1
    print("ok: registry pages match a fresh build")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="Regenerate to a tempdir and diff against committed pages. CI mode.",
    )
    args = parser.parse_args()
    if args.check:
        return check()
    return build(SITE_ROOT)


if __name__ == "__main__":
    sys.exit(main())
