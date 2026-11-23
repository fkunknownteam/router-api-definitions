# How to add a model

This is the checklist a contribution must satisfy. Read `docs/registry-layout.md`
first for the naming rules and `docs/changelog-process.md` for versioning.

## 0. Prerequisites

* You have the device in front of you, or a decoded firmware image plus the UI
  JavaScript it serves.
* You are allowed to change its settings. Several steps below reboot it and one
  wipes it.
* `python3 -m pip install -r tools/requirements.txt`

## 1. Identify the model and the firmware line

Get the exact strings, not the marketing name:

```
adv_firm_ver   V15.03.06.50_multi
adv_hard_ver   V1.0
```

Both come from `status.system` after login. The firmware string decides the
variant directory; the hardware revision goes in `hardwareVersions`.

Two builds with different `adv_firm_ver` prefixes are two variants even if the
model is the same. Do not average them into one file.

## 2. Recover the API from the device itself

Load the device's own UI and read what it sends. This is the only acceptable
source of endpoint truth.

1. Open the router UI with browser devtools on the Network tab.
2. Visit each page and use each control once. Record: request path, method,
   body encoding, every form field, and the reply.
3. Save the UI's JavaScript. Field names, accepted values and client-side
   validation limits are in there even when a page is not obvious.
4. For endpoints the UI never calls (rare), check the decoded firmware for the
   handler name. If you cannot make the device answer it, it does not go in.

Record what you saw in the `evidence` field of each endpoint. "Seen in
`js/wireless_ssid.js` and confirmed by a live save" is checkable; "from the
firmware" is not.

## 3. Create the variant directory

```bash
mkdir -p registry/<vendor>/<model>/v<hardware>-fw<firmware>
```

Copy the closest existing variant of the same model as a starting point, then
delete everything you did not verify. A short honest file beats a long guessed
one — see the v3.0 AC10 definition, which deliberately lists seven endpoints and
says so in `provenance.notes`.

## 4. Fill in `api.json`

Every endpoint needs:

* `id` — dotted, lowercase, stable forever. `wifi.basic.set`, not `setWifi`.
* `path`, `method` — exactly as captured.
* `verified` — one of the schema's values: `live-capture` (reply captured),
  `live-roundtrip` (read, wrote, read back), `app-observed` (exercised by a
  shipping client but no raw reply kept), `ui-code` (only the device's own
  JavaScript proves it), `not-captured` (code path exists, never exercised),
  `absent` (handler not compiled in).
* `evidence` — one line naming the file or the capture.

Add `response.shapeUnverified: true` whenever you know the endpoint exists but
never saw its reply. Do not fill `response.fields` from a guess.

Safety fields are not optional decoration: `writes`, `risk`,
`requiresConfirmation`. A `destructive` endpoint without
`requiresConfirmation: true` will be rejected in review.

## 5. Fill in `capabilities.json`

Same `definitionVersion` as `api.json`. For every feature:

* `full` — you verified both a getter and a working setter
* `read-only` — the device reports it, no verified setter
* `unknown` — the device may do it, nothing proves either way
* **omit the key** — the definition proves the device cannot do it

Omit rather than guess. A client shows an `unknown` control and hides an absent
one; marking something absent when it works removes a feature from the app, and
marking something `full` when the setter is unverified produces failed writes.

`limits` are the numbers the UI enforces. Take them from the JavaScript
(`maxlength`, `min`, `max`, array caps), not from what feels reasonable.

## 6. Validate and test

```bash
python3 tools/build_index.py
python3 tools/validate.py
python3 tools/selftest.py
bash tools/java_selftest.sh
```

All four must pass. `selftest` runs the generic client and the Android engine
against a fake router built from your definition: if your declaration is
internally inconsistent (a write behind a `read-only` feature, a field the body
builder cannot encode), it fails there rather than on a user's router.

## 7. Add the changelog entry and open the PR

One entry in `CHANGELOG.md` under the new version. The PR description must
contain:

* model and exact firmware string
* what hardware you tested on
* which endpoints are `live-roundtrip` versus `app-observed`
* anything you could not verify, stated plainly

## Review checklist

A reviewer rejects the PR if:

- [ ] any endpoint has no `evidence`
- [ ] `shapeUnverified` is missing on an endpoint whose reply was never seen
- [ ] a write endpoint lacks `writes: true` or a `risk`
- [ ] a `destructive` endpoint lacks `requiresConfirmation`
- [ ] `capabilities.json` marks a feature `full` with no verified setter
- [ ] `definitionVersion` was not bumped, or differs between the two files
- [ ] the index was not regenerated
- [ ] a note states behaviour nobody observed
- [ ] credentials, tokens, serial numbers or a real WAN IP appear anywhere

## Adding a second vendor

Nothing in the tools is Tenda-specific. Add `registry/<vendor>/<model>/...`
and, if the vendor needs a body or cipher form the schema does not express, add
it to the schema with a `schemaVersion` bump and to the executors in `tools/` and
`android/`. Do not add vendor branches to the executors; extend the vocabulary.
