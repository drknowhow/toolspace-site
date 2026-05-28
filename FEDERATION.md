# Federation — Publishing tools into toolspace.yepgent.com

This registry federates with anyone who publishes a
`.well-known/install-manifests.json` index at a stable identity. If
your tools follow the [install-manifest spec][spec] and you want them
listed at `toolspace.yepgent.com`, you don't need a PR per tool — you
publish your own index, get added to the allowlist once, and every
sync pulls your latest catalog automatically.

[spec]: https://github.com/drknowhow/install-manifest-spec

## How it works

1. You host an index file at a kind-specific discovery URL. Three
   identity kinds are supported:

   | kind     | id                          | discovery URL                                                    |
   |----------|-----------------------------|------------------------------------------------------------------|
   | `github` | `owner/repo`                | `https://raw.githubusercontent.com/owner/repo/main/.well-known/install-manifests.json` |
   | `https`  | bare hostname               | `https://hostname/.well-known/install-manifests.json`            |
   | `atproto`| `did:plc:…` or `did:web:…`  | (v2 — pending AT Protocol lexicon)                               |

2. The index conforms to the [well-known-index v1 schema][wki]. It
   lists each tool you publish: id, manifest URL, manifest version,
   status, optional summary + tags.

3. You get added to `publishers.json` in this repo via a one-time PR
   from a maintainer.

4. A GitHub Action on this repo runs daily at 13:17 UTC, fetches your
   index, validates every tool's install-manifest, and re-renders
   `manifests.json`. The auto-sync PR squash-merges itself the moment
   CI is green.

[wki]: https://github.com/drknowhow/install-manifest-spec/blob/main/schema/well-known-index-v1.json

## Adding yourself as a publisher

1. **Author your install-manifests.** Each tool lives at its own JSON
   file conforming to the install-manifest spec (v0.4 is current; v0.3
   and v0.3.1 are still accepted). See the [spec repo's examples][ex].

2. **Publish your well-known index.** Drop a file at
   `.well-known/install-manifests.json` in whichever location matches
   your identity kind (top of the repo for `github`, web root for
   `https`).

   Minimum shape:

   ```json
   {
     "version": "1",
     "publisher": {
       "kind": "github",
       "id": "your-org/your-repo",
       "display_name": "Your Project"
     },
     "generated_at": "2026-05-28T00:00:00Z",
     "manifests": [
       {
         "id": "your-tool",
         "manifest_url": "https://raw.githubusercontent.com/your-org/your-repo/main/manifests/your-tool.v0.4.json",
         "manifest_version": "0.4",
         "status": "active",
         "summary": "One-sentence description (≤280 chars).",
         "tags": ["category", "another-tag"]
       }
     ]
   }
   ```

3. **Validate offline.** Clone toolspace-site and run:

   ```bash
   python scripts/discover_publishers.py --check
   ```

   …after temporarily editing `publishers.json` to include your entry.
   It will report fetch + validation status for every publisher.

4. **Open a one-time PR adding your publisher entry to
   `publishers.json`.** Template:

   ```json
   {
     "kind": "github",
     "id": "your-org/your-repo",
     "display_name": "Your Project",
     "homepage": "https://github.com/your-org/your-repo",
     "added_at": "YYYY-MM-DD",
     "added_by": "your-github-handle",
     "trust_tier": "standard",
     "notes": "Optional context for the maintainers."
   }
   ```

5. **That's it.** Once your PR merges, the daily federation-sync run
   picks you up automatically. Your subsequent tool additions, version
   bumps, and deprecations land in `manifests.json` with no further
   coordination — just update your `.well-known/install-manifests.json`
   and the next sync reflects it.

[ex]: https://github.com/drknowhow/install-manifest-spec/tree/main/examples

## Lifecycle: what `status` values do

Set on each entry in your index:

| index status   | what it means                                              | registry behavior          |
|----------------|------------------------------------------------------------|----------------------------|
| `active`       | Tool is current and supported.                             | listed as `stable`         |
| `deprecated`   | Replaced by a newer tool. Must include `deprecated_in_favor_of`. | listed as `deprecated`     |
| `broken`       | Self-reported broken; the manifest URL won't even fetch.   | skipped (not listed)       |
| `quarantined`  | Maintainer-side hold (e.g., suspected schema regression).  | skipped (not listed)       |

`broken` and `quarantined` are forward-compat hooks: a publisher can
signal "don't list this right now" without removing it from their
index. The registry simply omits these entries until they flip back
to `active`.

## Trust tiers

`publishers.json` carries a `trust_tier` per publisher:

- `standard` — default. No special treatment.
- `verified` — used for publishers the registry maintainers have
  explicitly vouched for (currently only `drknowhow/Yep` itself).
- `tentative` — reserved for newcomers under observation.

Tiers are informational in v1; they don't change discovery or
filtering. They exist so future registry UI can highlight verified
publishers.

A publisher can also include a `registry_status_map` override on its
allowlist entry. The Yep self-dogfood uses
`{"active": "example"}` to map its own active tools to the registry's
`example` status — preserving the visual line between Yep's own tools
and third-party publisher catalogs.

## What gets validated

When the daily sync (or `python scripts/discover_publishers.py
--check`) runs against your publisher:

1. Discovery URL is reachable and returns valid UTF-8 JSON within 2 MiB.
2. The JSON validates against well-known-index v1 (version=1, three
   required top-level keys, per-kind id pattern, manifest entry shape).
3. `publisher.kind` + `publisher.id` in the document match the
   allowlist entry.
4. Every `manifest_url` you list resolves to a valid install-manifest
   (parsed via the install-manifest CLI's version-aware loader).
5. The `tool.id` inside each fetched install-manifest matches the `id`
   in your index.

Any failure surfaces as a warning in the sync output. A single broken
manifest doesn't fail the run — it's skipped, and the rest of your
catalog still syncs.

## Removing a publisher

Two paths:

- **Soft removal**: flip all your entries to `broken` or
  `quarantined`. They drop out of `manifests.json` on the next sync;
  you stay on the allowlist for if you come back.
- **Hard removal**: open a PR removing your entry from
  `publishers.json`. The next sync drops all your tools.

## Questions

Open an issue against [drknowhow/toolspace-site][site].

[site]: https://github.com/drknowhow/toolspace-site
