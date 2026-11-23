# Repository layout

```
schema/                     JSON Schemas (draft 2020-12). Change only with a schemaVersion bump.
registry/
  index.json                Generated. Clients fetch this first.
  <vendor>/
    <model>/                e.g. ac10 — lowercase, matches modelSlug
      <variant>/            e.g. v1.0-fw15.03.06.50
        api.json            full endpoint map
        capabilities.json   feature flags and limits for the Android UI
tools/                      validator, index builder, generic client, fake router, self tests
android/                    the loader/executor the app uses, plus integration notes
docs/                       conventions, contribution rules, changelog process, verification
CHANGELOG.md                one entry per definitionVersion change
```

## Why vendor / model / variant, and not one file per model

A model is not one API. An AC10 with `V15.03.06.50_multi` is a plaintext JSON
device; the same AC10 with `V03.03.16.11_multi` encrypts every reply with a
per-request AES key. Same box, same UI family, incompatible wire format. Flatten
that into one file and the client has to guess which branch it is talking to at
runtime.

So the unit of truth is the triple **(vendor, model, firmware line)**:

* `vendor` — `tenda`
* `model` — `ac10`
* `variant` — `<hardware>-fw<firmware line>`, e.g. `v3.0-fw03.03.16.11`

One variant directory = one firmware family that behaves identically on the
wire. If two builds differ in a field name or a cipher, they are two variants.

## Naming rules

| Thing | Rule | Example |
|---|---|---|
| vendor dir | lowercase, no spaces | `tenda` |
| model dir | lowercase model slug | `ac10` |
| variant dir | `v<hardware>-fw<firmware>`, dots kept | `v1.0-fw15.03.06.50` |
| `model` field | exact marketing model, uppercase | `AC10` |
| `firmware.matchPrefix` | the shortest prefix that identifies the family | `V15.03.06` |
| endpoint `id` | dotted, stable, never renamed | `wifi.basic.set` |

The variant directory name must equal `api.json.variant` and
`capabilities.json.variant`; `tools/validate.py` fails if they drift apart.

## What belongs in `api.json`

Only what the firmware actually does. Per endpoint:

* `id` — the contract. Clients call this string, not the path.
* `path` and `method` — exactly what the device answers.
* `request.bodyEncoding` — `none`, `form`, `encrypted-form`, `multipart`, `json`.
* `request.fields` — every field the UI's own form sends, with `values` when the
  firmware accepts a fixed set.
* `response.kind` and `response.fields` — the shape, including nested
  `a.b` and array `a[].b` paths.
* `writes`, `risk`, `requiresConfirmation` — how dangerous the call is.
* `verified` — how it was established: `live-capture` (raw reply recorded),
  `live-roundtrip` (written, read back, matches), `app-observed` (a shipping
  client exercised it, raw reply not kept), `ui-code` (only the device's own
  JavaScript proves it), `not-captured` (the code path exists, never exercised),
  `absent` (the handler is not compiled into this firmware).
* `evidence` — one line a reviewer can check by hand.
* `response.shapeUnverified` — true when the endpoint exists but no reply was
  ever captured. Clients may call it and must treat every field as absent.

## What must never appear

* An endpoint guessed from a page name. If the UI does not call it and no reply
  was seen, it is not verified, and it does not go in.
* A field invented to make a payload look complete. These firmwares reset
  omitted fields to defaults, so an invented field is worse than a missing one.
* A "probably" in a field description. Say `shapeUnverified` instead.
* Any credential, session token or device serial from a real deployment.
