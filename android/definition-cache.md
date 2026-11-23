# Definition cache

How the app stores downloaded definitions, and what it does when it cannot reach
the network. A router's LAN usually has no internet, so the offline path is the
common path, not the fallback.

## Directory layout

```
filesDir/definitions/
  index.json                        last good copy of registry/index.json
  <sha256(api.json)>/
    api.json
    capabilities.json
  current                           one line: the api sha256 currently in use
```

Both files of a variant live under the hash **of the api.json**. The hash is the
cache key, so:

* a new definition never overwrites an old one;
* two devices on different firmware can be served from the same store;
* rollback means rewriting `current` with the previous hash — no re-download;
* a partial download (api.json present, capabilities.json missing) is detectable
  by the missing sibling, so the directory is either complete or absent.

Write both files to `<hash>.tmp/` and rename to `<hash>/` when both hashes match.
A rename is atomic on the same filesystem; a half-written definition is never
loadable.

## Fetch flow

```
1. index := GET registry/index.json            (10 s connect, 20 s read)
   on failure -> load filesDir/definitions/index.json
   both fail -> "no definitions available, connect once to set up"

2. variant := select(index, firmware, hardware)  longest matchPrefix wins
   no match -> tell the user the firmware is unknown; do not guess a variant

3. if filesDir/definitions/<variant.sha256.api>/api.json exists
      load it, skip the download
   else
      GET apiUrl, verify sha256, GET capabilitiesUrl, verify sha256

4. write both, update `current`, then load
```

A mismatch on either hash discards the download and keeps whatever is cached. A
definition that fails verification is never executed.

## Offline behaviour

| State | Behaviour |
|---|---|
| cached copy matches the index | use it, no network |
| index reachable, definition cached | use cache, no download |
| index unreachable, cache present | use cache, no warning |
| index unreachable, no cache | block setup, explain that one online session is needed |
| definition changed upstream | keep the cached one until the next successful fetch |
| hash mismatch on download | discard, keep cache, surface a one-line warning |

Never fall back to "some definition for this model" when the firmware does not
match. A wrong variant on an AES line fails at the first decryption and looks
indistinguishable from a wrong password — see the probe order in
`android/README.md`.

## Refreshing the index

24 hours, or when the user taps "check for updates". Do not refresh on every
launch: it wastes the one moment the phone may have connectivity and adds a
network dependency to opening the app.

## Eviction

Keep the current definition plus the previous one (for rollback). Delete any
other hash directory. Definitions are small — a few hundred KB in total — so this
is about tidiness, not space.

## Verifying a cache entry by hand

```bash
sha256sum filesDir/definitions/<hash>/api.json
# compare with $.definitions[?(@.path=="registry/tenda/ac10/...")].sha256.api
```

If the file is present but its hash does not match the directory name, treat the
entry as corrupt and delete it.
