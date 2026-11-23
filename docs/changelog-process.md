# Changelog process

Two different versions exist and they change for different reasons.

| Field | Scope | Bump when |
|---|---|---|
| `schemaVersion` | every file, same value everywhere | the JSON Schema changes shape in a way old clients cannot read (removed field, narrowed enum, changed meaning). Rare. |
| `definitionVersion` | one variant directory | the definition for that vendor/model/firmware changes at all. |

`schemaVersion` lives in `schema/*.schema.json` as a `const`. Bumping it is a
breaking release of the whole registry: every file is re-validated and every
client is expected to refuse a major it does not know.

## What counts as a definition change

Bump `definitionVersion` for:

* adding, removing or renaming an endpoint
* changing a path, method, field name, field values or default
* changing `writes`, `risk`, `requiresConfirmation`, `verified` or
  `shapeUnverified` — these drive client behaviour and safety prompts
* adding or correcting a `feature` mapping
* any change to `capabilities.json` (features or limits)
* correcting a note that was factually wrong about device behaviour

Do **not** bump for:

* `registry/index.json` regeneration alone (its `sha256` follows the file)
* whitespace or key order
* fixing a typo in prose that does not change meaning

## How to bump

```bash
# 1. edit the definition
$EDITOR registry/tenda/ac10/v3.0-fw03.03.16.11/api.json

# 2. set the same version in both files of that directory
#    api.json          "definitionVersion": "1.1.0"
#    capabilities.json "definitionVersion": "1.1.0"

# 3. regenerate the index and validate
python3 tools/build_index.py
python3 tools/validate.py

# 4. add a CHANGELOG entry under the new version
$EDITOR CHANGELOG.md
```

`tools/validate.py` compares each `api.json` with its version in git `HEAD` and
fails when the content changed but `definitionVersion` did not. That check is
what keeps clients from silently receiving different behaviour under a version
they already cached.

## Semver, applied to router definitions

The API is not code, so the usual reading needs pinning down:

* **PATCH** — no change to any path, field name, accepted value or flag. Notes,
  evidence text, extra fields in a response that were already there, correcting
  a `uiPage`.
* **MINOR** — additive. A new endpoint, a new optional field, a new feature key,
  a new `limits` entry, a newly verified response shape
  (`shapeUnverified` going true → false).
* **MAJOR** — a client that worked before breaks. A removed or renamed endpoint
  id, a changed path or method, a field that becomes required, a value removed
  from an enum, a feature that moves from `full` to `read-only`, or a change to
  `crypto`/`session` that alters the handshake.

Renaming an endpoint id is a MAJOR even though the device did not change:
clients key on the id. Add a new id, keep the old one marked deprecated for one
minor cycle, then remove it in the next major.

## CHANGELOG.md format

Newest first, one section per released definition version, grouped by variant.
Follow Keep a Changelog headings (`Added`, `Changed`, `Fixed`, `Removed`) but
always state the *device* consequence, not just the file edit.

```markdown
## tenda/ac10 v4.0-fw16.03 — 1.1.0 — 2026-12-04

### Added
- `wifi.schedule.get` / `wifi.schedule.set`, verified by round-trip on
  V16.03.11. The schedule table is sent as one packed string, documented under
  `request.listEncoding`.

### Fixed
- `wifi.basic.set` no longer lists `doubleBand` as optional. Omitting it resets
  the band-merge flag and splits the SSIDs on save.
```

Every entry answers "what would a user notice". "Updated fields" is not an entry.

## Releasing

1. `python3 tools/build_index.py && python3 tools/validate.py` clean.
2. `python3 tools/selftest.py` and `bash tools/java_selftest.sh` pass.
3. `git tag -a tenda-ac10-v4.0-fw16.03-1.1.0 -m "..."` — one tag per changed
   variant, so a client can pin an exact document.
4. Push the tag with the commit. The `raw.githubusercontent.com` URLs are
   branch-based (`main`); the tag is the immutable record of what shipped.

Clients that need immutability fetch by commit SHA or tag in the URL instead of
`main`. The index records the `sha256` either way, so a mismatch is caught even
when the branch moves.
