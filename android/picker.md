# The picker: vendor → model → firmware

The app opens to a picker instead of guessing what is on the other end of the
wire. Three taps, no login spent, no wrong-cipher attempts.

```
┌─────────────────────────────┐
│ Pick your router            │
│                             │
│   Tenda                  ▸  │   step 1: vendors from the index
│   TP-Link                ▸  │
└─────────────────────────────┘
              ↓
┌─────────────────────────────┐
│ Tenda                       │
│                             │
│   AC10       3 firmware ▸   │   step 2: models that vendor documents
└─────────────────────────────┘
              ↓
┌─────────────────────────────┐
│ Tenda AC10                  │
│                             │
│ ● Hardware V1.0 — V15.03.06.50_multi
│ ○ Hardware V3.0 — V03.03.16.11_multi
│ ○ Hardware V4.0 — V16.03
└─────────────────────────────┘
              ↓
        download that variant,
        verify sha256, execute
```

Every row comes from `registry/index.json`. Nothing is hardcoded in the app: add
a vendor to the registry and it appears here on the next index refresh, with no
app release.

## Why a picker and not auto-detect

Auto-detect is the nicer demo and the worse product, on these devices
specifically:

* The firmware string that separates variants (`adv_hard_ver`, `adv_firm_ver`)
  is only readable **after** login.
* The login handshake itself differs per line — plaintext on fw15, AES on fw03
  and fw16. So the app cannot even log in without first committing to a
  variant.
* A wrong commit fails at decryption, which is indistinguishable from a wrong
  password. Worst case the app burns the router's remaining attempts (5 on
  these builds) guessing.

The picker removes the guess. The hardware label on the box ("V3.0") is printed
on the sticker, so the user has the information the app cannot obtain.

Auto-detect still has a place: **after** a successful login, read `adv_firm_ver`
and check it against the picked variant's `matchPrefix`. If they disagree, warn
and offer the correct variant. That is a correction, not a guess — the session
already exists and no attempt is spent.

## The data behind it

`registry/index.json` carries exactly what the three screens need:

```json
{
  "vendorNames": { "tenda": "Tenda" },
  "vendors": {
    "tenda": [{
      "model": "AC10",
      "slug": "ac10",
      "variantCount": 3,
      "variants": [{
        "path": "registry/tenda/ac10/v3.0-fw03.03.16.11",
        "label": "Hardware V3.0 — firmware V03.03.16.11_multi",
        "apiUrl": "...",
        "capabilitiesUrl": "...",
        "definitionVersion": "1.0.0",
        "firmware": "V03.03.16.11_multi",
        "matchPrefix": "V03.03.16",
        "hardwareVersions": ["V3.0"],
        "endpointCount": 7,
        "sha256": { "api": "...", "capabilities": "..." }
      }]
    }]
  }
}
```

`label` is generated from `hardwareVersions` and `firmware.verified` by
`tools/build_index.py`, so it cannot drift from the definition it names.

## Speed on first open

The index is one small JSON fetch (tens of KB). The picker renders from cache
first, then refreshes in the background:

```java
// instant: whatever was cached last time
Picker p = Picker.fromCache(filesDir + "/definitions");
render(p);

// then, without blocking the first paint
executor.submit(() -> {
    Map<String, Object> fresh = repo.fetchIndexDocument();  // also updates the cache
    Picker updated = Picker.fromIndex(fresh);
    runOnUiThread(() -> render(updated));
});
```

On a phone joined to a router with no internet, the cached picker is the only
one that loads — and it is enough, because the definition files themselves are
cached too.

## API

```java
Picker p = Picker.fromIndex(indexDoc);      // or Picker.fromCache(dir)
p.vendors()                                 // ["tenda"]
p.vendorName("tenda")                       // "Tenda"
p.models("tenda")                           // [Model{slug:"ac10", name:"AC10", variantCount:3}]
p.variants("tenda", "ac10")                 // 3 Variant, each with .label
p.variant("tenda", "ac10", chosen.path)     // that one Variant, or null
```

`variant()` returns **null** for an unknown vendor or model. It never falls back
to "some definition for this model" — a silent fallback is how a user ends up
driving a V3.0 router with a V1.0 map.

## Then execute

```java
Variant v = p.variant("tenda", "ac10", picked.path);
Definition d = repo.fetch(v);               // downloads both files, verifies sha256
RouterClient c = new RouterClient(d, host, 0, false);
c.setCredentials("admin", password);
c.login();
Object st = c.call("status.router", null, false);
```

From here nothing is model-specific. The paths, fields, cipher and login flow all
come from the document the user's pick resolved to.

## Verified

`PickerSelftest` in `android/engine/test/` renders all three steps from the real
`registry/index.json`, resolves a pick to a loaded `Definition`, and asserts an
unknown pick returns null. It runs in `tools/java_selftest.sh` and in CI.
