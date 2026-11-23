# Universal router control — API definitions

Machine-readable API definitions for every router model we have reverse
engineered, so one generic client can drive all of them.

The Android app ships **no endpoints**. It downloads a definition that matches
the device in front of it, verifies it against a published hash, and executes it.
Adding a router model is a data change here, never an app release.

```
client                          this repo
  │
  ├─ GET registry/index.json ──▶ every vendor/model/variant, firmware prefix,
  │                              sha256 of both files
  ├─ pick the variant whose firmware prefix matches the device
  ├─ GET .../api.json + capabilities.json
  ├─ verify sha256 ────────────▶ mismatch = discard, keep the cached copy
  └─ execute endpoints by id ──▶ paths, fields, ciphers all come from the file
```

## What is in here

| Path | What it is |
|---|---|
| `registry/` | the definitions, organised by vendor → model → firmware variant |
| `registry/index.json` | generated manifest: what exists, how to fetch it, its hash |
| `schema/` | JSON Schemas (draft 2020-12) for definitions, capabilities, index |
| `tools/` | validator, index builder, generic client, fake router, self tests |
| `android/` | the loader/executor the app uses, and how to integrate it |
| `docs/` | naming rules, changelog process, verification levels |

## Coverage

One vendor, many firmware lines — because the same model is not one API.

| Vendor | Model | Variant | Firmware | Endpoints | Verified |
|---|---|---|---|---|---|
| Tenda | AC10 | `v1.0-fw15.03.06.50` | `V15.03.06.50_multi` | 102 | live capture on hardware |
| Tenda | AC10 | `v3.0-fw03.03.16.11` | `V03.03.16.11_multi` | 7 | live round-trip on hardware |
| Tenda | AC10 | `v4.0-fw16.03` | `V16.03.x` | 36 | observed from the shipping Android app |

The three AC10 lines are genuinely different devices on the wire:

* **v1.0 / fw15** — plaintext JSON, no encryption, no USB subsystem compiled in.
* **v3.0 / fw03** — AES-128-CBC on every authenticated reply, key fetched from
  `/goform/stokCfg` per request. Deliberately partial: only what was exercised
  on that build is claimed.
* **v4.0 / fw16** — AES like v3.0 but a different login reply, and two field
  traps documented in the file (`doubleBand` is a band-merge flag, not a radio
  count; `wanStatus` uses `1310209` as its connected sentinel).

A client picks by firmware prefix, so all three coexist without ambiguity.

## Using it

### From the Android app

See `android/README.md` for the full flow. The short version:

```java
DefinitionRepository repo = new DefinitionRepository(
        DefinitionRepository.DEFAULT_REGISTRY, filesDir + "/definitions");
List<Variant> variants = repo.fetchIndex();                 // cache 24 h
Variant v = DefinitionRepository.select(variants, "V15.03.06.50_multi", "V1.0");
Definition d = repo.fetch(v);                               // sha256 verified

RouterClient c = new RouterClient(d, "192.168.0.1", 0, false);
c.setCredentials("admin", password);
c.login();
Object status = c.call("status.router", null, false);        // read
if (d.supports("wifi.guest", true)) {
    c.call("wifi.guest.set", values, true);                  // write, gated
}
```

Gate the UI on `capabilities.json`, never on the model name:

* `full` → show the control, allow the write
* `read-only` → show the data, disable the control
* `unknown` → show the control, expect failure, do not claim the device can't
* key absent → the definition proves the device cannot do it; hide it

### From a shell

```bash
python3 -m pip install -r tools/requirements.txt

# what can this definition do, with no network?
python3 tools/routerapi.py registry/tenda/ac10/v1.0-fw15.03.06.50/api.json --list

# talk to a real router
python3 tools/routerapi.py registry/tenda/ac10/v4.0-fw16.03/api.json \
    --host 192.168.0.1 --password 'admin' --call status.router
```

`tools/routerapi.py` contains no vendor logic either — it reads the definition it
is given, including the cipher parameters and the login handshake.

## Adding a model

`CONTRIBUTING.md` is the checklist. The parts people get wrong:

1. **One variant per firmware line.** Same model, different `adv_firm_ver`
   prefix → two directories.
2. **Only what you observed.** No endpoint inferred from a page name, no field
   invented to make a payload look complete. These firmwares reset omitted fields
   to defaults, so an invented field breaks a real network.
3. **Capabilities are conservative.** `full` requires a verified setter. Omit a
   feature rather than guess.
4. **Bump `definitionVersion`** in both files of the directory and add a
   `CHANGELOG.md` entry. The validator fails the build otherwise.

## Verifying a contribution

```bash
python3 tools/build_index.py
python3 tools/validate.py
python3 tools/selftest.py
bash tools/java_selftest.sh
```

`validate.py` enforces schema validity, cross-file consistency, the feature
mapping, the version bump and the index hashes. The two self tests run the Python
client and the Java engine against a fake router built from each definition, so a
declaration the clients cannot execute fails in CI instead of on a user's router.

Read `docs/verification.md` for exactly what those tests do and do not prove. In
short: they prove the document is executable, not that the device behaves that
way. Device truth lives in each endpoint's `verified` and `evidence` fields, and
that is why the levels are recorded per endpoint instead of per file.

## Design rules

* **The id, not the path, is the contract.** Clients call `wifi.basic.set`; the
  path can be corrected in a patch release without touching client code.
* **Absence is information.** A missing feature key means "this device cannot do
  it"; `unknown` means "nobody has checked". The two are never merged.
* **Safety is data.** `writes`, `risk` and `requiresConfirmation` ship with the
  endpoint so every client gates dangerous calls the same way.
* **No secrets.** No credentials, tokens, serials or real WAN addresses. Example
  values are placeholders.

## Publishing this repository

The URLs baked into `registry/index.json` and `android/README.md` assume the repo
lives at `fkunknownteam/router-api-definitions`. If you fork or move
it, change the owner in two places and regenerate:

```bash
# 1. tools/build_index.py
REPO = "your-user/router-api-definitions"

# 2. android/README.md — the REGISTRY_BASE constant
python3 tools/build_index.py
python3 tools/validate.py
```

Then create the remote and push with a token that has `repo` scope:

```bash
GH_TOKEN=ghp_xxx bash tools/publish.sh <owner> [repo-name]
```

The script creates the repository if it does not exist, sets `origin`, and pushes
`main` without writing the token into `.git/config`. It needs `curl` and `jq`.

## Licence

Definitions are facts about devices you own; the schemas and tooling are MIT.
See `LICENSE`.
