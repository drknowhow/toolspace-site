# BUGS

Non-critical bugs and smells filed during work, per Yep's bug-discipline
behavior. Critical bugs (data loss, security, broken core path) are
fixed inline; everything else lands here.

## Open

### sync_from_spec.py writes platform-native line endings on Windows

- **Severity:** low (cosmetic / noise)
- **Location:** `scripts/sync_from_spec.py` — `dst.write_text(src.read_text(...))` round-trip
- **Symptom:** After running `python scripts/sync_from_spec.py` on
  Windows, `git status` shows the synced files as modified with
  `warning: in the working copy of '...', CRLF will be replaced by LF
  the next time Git touches it`. `git diff` produces zero content
  output — the working tree has CRLF but the index (and `.gitattributes`
  enforcement) keeps LF, so `git add` normalizes silently and there is
  no real diff in the commit. Cosmetic only, but creates noisy
  `git status` output and forces a `git checkout --` to clear.
- **Suggested fix:** In `sync_from_spec.py`, write bytes with explicit
  `\n` (open in `"wb"` mode and write `src.read_bytes()` after
  normalizing CRLF→LF, OR use `dst.write_text(text, newline="\n")`).
  Then the working tree matches the index on every platform.
- **Why not now:** Pure cosmetic; doesn't affect deploy, CI, or
  manifest correctness. Worth folding into the next sync-script
  touch rather than its own PR.

## External

(none)
