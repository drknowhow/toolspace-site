# toolspace-site

Source for [toolspace.yepgent.com](https://toolspace.yepgent.com) &mdash; the
public home of the [install-manifest-spec](https://github.com/drknowhow/install-manifest-spec):
an open manifest spec for **personal, stateful AI agents** &mdash; the long-running
kind that hold memory, schedule themselves, learn their operator's preferences over
weeks and months, and act between conversations.

Toolspace is the structured JSON layer that sits on top of [AGENTS.md](https://agents.md/).
Where AGENTS.md gives an agent prose context about a repo or workspace, toolspace gives
it a machine-readable contract for the tools, memories, schedules, and behaviors that make
up a personal agent's surface.

Not a registry for coding-assistant plugins &mdash; that lane is being solved well by
others, and the manifest shape that fits a per-task coding helper is structurally
different from what a persistent personal agent needs. The canonical demo is
[Yep](https://yepgent.com/) and its federated counterpart Vi; every primitive ships
in production there before it lands in the spec.

The site hosts the schemas, reference manifests, and the curated index. Manifests can
live anywhere &mdash; raw GitHub URLs, third-party mirrors, or here.

## Layout

```
schemas/install-manifest-v0.1.json   # mirrored from install-manifest-spec
schemas/install-manifest-v0.2.json   # mirrored from install-manifest-spec
schemas/install-manifest-v0.3.json   # mirrored from install-manifest-spec (current)
examples/gmail.json                   # reference manifest (v0.1)
examples/gmail.v0.2.json              # reference manifest (v0.2)
examples/gmail.v0.3.json              # reference manifest (v0.3, current)
manifests.json                        # registry index (lists every version)
index.html                            # landing page
netlify.toml + _headers               # CORS + cache headers
scripts/sync_from_spec.py             # mirror schemas/examples from spec
scripts/validate_manifests_index.py   # structural check on manifests.json
.github/workflows/check.yml           # CI: byte-identity + index validity
```

## Adding a manifest

1. Open a PR adding an entry to `manifests.json`. Required fields:
   `id`, `name`, `description`, `capabilities` (list), `manifest_url`
   (https), `status` (`stable` / `preview` / `example` / `deprecated`).
2. The `manifest_url` must validate against the schema in `/schemas/`.
   Use the [install-manifest CLI](https://github.com/drknowhow/install-manifest-spec/tree/main/cli)
   to verify before opening the PR:
   ```bash
   pip install install-manifest
   install-manifest validate <your-manifest-url>
   ```
3. CI runs `validate_manifests_index.py` on every PR.

## Syncing from the spec

When `install-manifest-spec` ships a new schema version or updated example,
mirror it here:

```bash
# from a sibling clone of install-manifest-spec
python scripts/sync_from_spec.py
git diff
git commit -am "sync: schema/examples from install-manifest-spec @ <sha>"
```

CI runs `sync_from_spec.py --check` on every push to catch silent drift.

## Deploy

Netlify, deployed from `main`. `toolspace.yepgent.com` is configured as the
custom domain on the Netlify site; DNS lives in GoDaddy and points at
Netlify's load balancer.

## License

[MIT](LICENSE)
